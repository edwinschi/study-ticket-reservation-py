import { check, fail, sleep } from 'k6';
import { Counter } from 'k6/metrics';
import http from 'k6/http';

/*
  Quantity stress test.

  The goal is to create real contention on ticket_types.reserved_quantity. A 409 response is an
  expected business conflict when available stock is gone; a 500 or timeout means the API failed
  under load. The teardown consistency check verifies that PostgreSQL invariants still hold.
*/
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 399 }, 409));

const BASE_URL = __ENV.BASE_URL || 'http://api:8000';
const SLEEP_SECONDS = Number(__ENV.SLEEP_SECONDS || '0.1');

export const options = {
  vus: Number(__ENV.VUS || '100'),
  duration: __ENV.DURATION || '30s',
  noCookiesReset: true,
  thresholds: {
    // 409 is explicitly expected under contention and is excluded by expectedStatuses above.
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<2000', 'p(99)<5000'],
    server_errors: ['count==0'],
  },
};

const serverErrors = new Counter('server_errors');
const quantityCreated = new Counter('quantity_reservations_created');
const quantityConflicts = new Counter('quantity_reservation_conflicts');
const quantityCancelled = new Counter('quantity_reservations_cancelled');
const quantityConfirmed = new Counter('quantity_reservations_confirmed');
let sessionReady = false;

function jsonHeaders() {
  return { 'Content-Type': 'application/json' };
}

function requestParams() {
  return {
    headers: jsonHeaders(),
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
  console.log(`[setup] quantity seed event_id=${seed.event_id} ticket_type_id=${seed.ticket_type_id}`);
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
    idempotency_key: idempotencyKey('k6-quantity'),
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

function maybeFinalizeReservation(reservationID) {
  if (!reservationID) {
    return;
  }

  /*
    This deliberately exercises lifecycle races under load.
    Some successful reservations are cancelled, some are confirmed, and some are left reserved
    for the expiration worker or later manual inspection.
  */
  const roll = Math.random();
  if (roll < 0.25) {
    const response = http.post(`${BASE_URL}/v1/reservations/${reservationID}/cancel`, '{}', requestParams());
    recordServerError(response, 'cancel-quantity');
    check(response, { 'quantity cancel returned 200': (r) => r.status === 200 });
    if (response.status === 200) {
      quantityCancelled.add(1);
    }
    return;
  }

  if (roll < 0.5) {
    const response = http.post(`${BASE_URL}/v1/reservations/${reservationID}/confirm`, '{}', requestParams());
    recordServerError(response, 'confirm-quantity');
    check(response, { 'quantity confirm returned 200': (r) => r.status === 200 });
    if (response.status === 200) {
      quantityConfirmed.add(1);
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

  const reservationID = reserveQuantity(seed);
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
