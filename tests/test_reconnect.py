from reconnect import ReconnectPolicy, error_code, is_fatal


class _Err(Exception):
    def __init__(self, code):
        super().__init__(f"code {code}")
        self.code = code


def test_fatal_codes():
    assert is_fatal(_Err(1008))  # Policy-Verstoß, z. B. API-Key
    assert is_fatal(_Err(1007))
    assert is_fatal(_Err(403))
    assert is_fatal(ValueError("kaputte Konfiguration"))


def test_retryable_codes():
    assert not is_fatal(_Err(1006))  # abnormaler Abbruch
    assert not is_fatal(_Err(1011))  # Serverfehler
    assert not is_fatal(_Err(429))  # Kontingent kurz erschöpft
    assert not is_fatal(_Err(503))
    assert not is_fatal(OSError("Netzwerk weg"))
    assert not is_fatal(TimeoutError())


def test_error_code_reads_websockets_rcvd():
    class Rcvd:
        code = 1011

    exc = Exception()
    exc.rcvd = Rcvd()
    assert error_code(exc) == 1011


def test_backoff_grows_and_caps():
    policy = ReconnectPolicy(base_delay=1, max_delay=8, rand=lambda: 1.0)
    assert [policy.next_delay() for _ in range(6)] == [1, 2, 4, 8, 8, 8]


def test_jitter_is_half_to_full():
    assert ReconnectPolicy(base_delay=4, rand=lambda: 0.0).next_delay() == 2


def test_max_attempts():
    policy = ReconnectPolicy(max_attempts=2)
    policy.next_delay()
    assert policy.can_retry()
    policy.next_delay()
    assert not policy.can_retry()


def test_stable_connection_resets_attempts():
    policy = ReconnectPolicy(stable_after=60)
    policy.next_delay()
    policy.next_delay()
    policy.connection_ended(5)
    assert policy.attempts == 2
    policy.connection_ended(120)
    assert policy.attempts == 0


def test_huge_attempt_count_does_not_overflow():
    policy = ReconnectPolicy(max_delay=30, max_attempts=10**9)
    policy.attempts = 5000
    assert policy.next_delay() <= 30
