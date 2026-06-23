import { check, fail, sleep } from 'k6';
import { Counter } from 'k6/metrics';
import http from 'k6/http';

/*
  Mixed stress test.

  This is the closest local approximation of real traffic in the project: anonymous users reserve
  quantity tickets or seats, then some cancel, some confirm, and some leave holds for expiration.
  The test intentionally accepts 409 conflicts and fails on server errors or failed consistency.
*/
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 399 }, 409));

const BASE_URL = __ENV.BASE_URL || 'http://api:8000';
const SLEEP_SECONDS = Number(__ENV.SLEEP_SECONDS || '0.1');
const QUANTITY_FLOW_RATIO = Number(__ENV.QUANTITY_FLOW_RATIO || '0.6');

export const options = {
  vus: Number(__ENV.VUS || '100'),
  duration: __ENV.DURATION || '30s',
  noCookiesReset: true,
  thresholds: {
    // 409 means expected contention; real failures are 5xx, timeouts, or failed consistency.
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<2000', 'p(99)<5000'],
    server_errors: ['count==0'],
  },
};

const serverErrors = new Counter('server_errors');
const quantityCreated = new Counter('quantity_reservations_created');
const quantityConflicts = new Counter('quantity_reservation_conflicts');
const seatCreated = new Counter('seat_reservations_created');
const seatConflicts = new Counter('seat_reservation_conflicts');
const cancelled = new Counter('reservations_cancelled');
const confirmed = new Counter('reservations_confirmed');
let sessionReady = false;

function requestParams() {
  return {
    headers: { 'Content-Type': 'application/json' },
    timeout: '5s',
  };
}

function recordServerError(response, label) {
  if (response.status === 0 || response.status >= 500) {
    serverErrors.add(1);
    console.error(`[${label}] unexpected server error status=${response.status} body=${response.body}`);
  }
}

function idempotencyKey(prefix) {
  return `${prefix}-${__VU}-${__ITER}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function loadSeed() {
  const response = http.post(`${BASE_URL}/v1/admin/stress/seed`, null, { timeout: '10s' });
  recordServerError(response, 'seed');

  if (
    !check(response, {
      'stress seed created': (r) => r.status === 201,
    })
  ) {
    fail(`could not create stress seed: status=${response.status} body=${response.body}`);
  }

  const seed = response.json();
  console.log(
    `[setup] mixed seed event_id=${seed.event_id} ticket_type_id=${seed.ticket_type_id} seats=${seed.seat_ids.length}`,
  );
  return seed;
}

function ensureAnonymousSession() {
  if (sessionReady) {
    return true;
  }

  const response = http.post(`${BASE_URL}/v1/sessions/anonymous`, null, { timeout: '5s' });
  recordServerError(response, 'anonymous-session');

  sessionReady = check(response, {
    'anonymous session created': (r) => r.status === 201,
  });
  return sessionReady;
}

function reserveQuantity(seed) {
  const payload = JSON.stringify({
    event_id: seed.event_id,
    ticket_type_id: seed.ticket_type_id,
    quantity: 1,
    idempotency_key: idempotencyKey('k6-mixed-quantity'),
  });

  const response = http.post(`${BASE_URL}/v1/reservations/quantity`, payload, requestParams());
  recordServerError(response, 'reserve-quantity');

  check(response, {
    'quantity reservation returned 201 or 409': (r) => r.status === 201 || r.status === 409,
  });

  if (response.status === 201) {
    quantityCreated.add(1);
    return response.json().reservation_id;
  }
  if (response.status === 409) {
    quantityConflicts.add(1);
  }
  return '';
}

function reserveSeat(seed) {
  const seatID = seed.seat_ids[Math.floor(Math.random() * seed.seat_ids.length)];
  const payload = JSON.stringify({
    event_id: seed.event_id,
    seat_ids: [seatID],
    idempotency_key: idempotencyKey('k6-mixed-seat'),
  });

  const response = http.post(`${BASE_URL}/v1/reservations/seats`, payload, requestParams());
  recordServerError(response, 'reserve-seat');

  check(response, {
    'seat reservation returned 201 or 409': (r) => r.status === 201 || r.status === 409,
  });

  if (response.status === 201) {
    seatCreated.add(1);
    return response.json().reservation_id;
  }
  if (response.status === 409) {
    seatConflicts.add(1);
  }
  return '';
}

function maybeFinalizeReservation(reservationID) {
  if (!reservationID) {
    return;
  }

  /*
    k6 should not only reserve inventory. Real systems also race on state transitions:
    some clients abandon carts, some complete checkout, and some leave reservations for the
    expiration worker. This keeps the database lifecycle paths hot during the test.
  */
  const roll = Math.random();
  if (roll < 0.25) {
    const response = http.post(`${BASE_URL}/v1/reservations/${reservationID}/cancel`, '{}', requestParams());
    recordServerError(response, 'cancel-reservation');
    check(response, { 'cancel returned 200': (r) => r.status === 200 });
    if (response.status === 200) {
      cancelled.add(1);
    }
    return;
  }

  if (roll < 0.5) {
    const response = http.post(`${BASE_URL}/v1/reservations/${reservationID}/confirm`, '{}', requestParams());
    recordServerError(response, 'confirm-reservation');
    check(response, { 'confirm returned 200': (r) => r.status === 200 });
    if (response.status === 200) {
      confirmed.add(1);
    }
  }
}

export function setup() {
  return loadSeed();
}

export default function (seed) {
  if (!ensureAnonymousSession()) {
    sleep(SLEEP_SECONDS);
    return;
  }

  const reservationID = Math.random() < QUANTITY_FLOW_RATIO ? reserveQuantity(seed) : reserveSeat(seed);
  maybeFinalizeReservation(reservationID);
  sleep(SLEEP_SECONDS);
}

export function teardown() {
  const response = http.get(`${BASE_URL}/v1/admin/stress/assert-consistency`, { timeout: '10s' });
  recordServerError(response, 'assert-consistency');

  if (response.status !== 200 || response.json().ok !== true) {
    fail(`database consistency assertion failed: status=${response.status} body=${response.body}`);
  }
  console.log('[teardown] database consistency assertion passed');
}
