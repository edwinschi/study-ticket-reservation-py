import { check, fail, sleep } from 'k6';
import { Counter } from 'k6/metrics';
import http from 'k6/http';

/*
  Seat stress test.

  Many virtual users choose random seats from the same event. This is designed to exercise
  SELECT FOR UPDATE ordering and the partial unique index that allows only one active reservation
  per seat. 409 is expected when a user loses the race for a seat.
*/
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 399 }, 409));

const BASE_URL = __ENV.BASE_URL || 'http://api:8000';
const SLEEP_SECONDS = Number(__ENV.SLEEP_SECONDS || '0.1');

export const options = {
  vus: Number(__ENV.VUS || '100'),
  duration: __ENV.DURATION || '30s',
  noCookiesReset: true,
  thresholds: {
    // Seat conflicts are expected when many users pick the same seat.
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<2000', 'p(99)<5000'],
    server_errors: ['count==0'],
  },
};

const serverErrors = new Counter('server_errors');
const seatCreated = new Counter('seat_reservations_created');
const seatConflicts = new Counter('seat_reservation_conflicts');
const seatCancelled = new Counter('seat_reservations_cancelled');
const seatConfirmed = new Counter('seat_reservations_confirmed');
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

function randomSeatID(seed) {
  return seed.seat_ids[Math.floor(Math.random() * seed.seat_ids.length)];
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
  console.log(`[setup] seats seed event_id=${seed.event_id} seats=${seed.seat_ids.length}`);
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

function reserveSeat(seed) {
  const payload = JSON.stringify({
    event_id: seed.event_id,
    seat_ids: [randomSeatID(seed)],
    idempotency_key: idempotencyKey('k6-seat'),
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
    Seat confirmations intentionally keep the seat unavailable, while cancellations release it.
    Mixing both operations makes the test exercise the partial unique index across state changes.
  */
  const roll = Math.random();
  if (roll < 0.2) {
    const response = http.post(`${BASE_URL}/v1/reservations/${reservationID}/cancel`, '{}', requestParams());
    recordServerError(response, 'cancel-seat');
    check(response, { 'seat cancel returned 200': (r) => r.status === 200 });
    if (response.status === 200) {
      seatCancelled.add(1);
    }
    return;
  }

  if (roll < 0.4) {
    const response = http.post(`${BASE_URL}/v1/reservations/${reservationID}/confirm`, '{}', requestParams());
    recordServerError(response, 'confirm-seat');
    check(response, { 'seat confirm returned 200': (r) => r.status === 200 });
    if (response.status === 200) {
      seatConfirmed.add(1);
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

  const reservationID = reserveSeat(seed);
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
