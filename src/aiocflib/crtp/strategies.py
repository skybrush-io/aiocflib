from typing import Callable, List, Union

__all__ = (
    "BackoffPollingStrategy",
    "DefaultPollingStrategy",
    "DefaultResendingStrategy",
    "NoPollingStrategy",
    "PatientResendingStrategy",
    "PollingStrategy",
    "ResendingStrategy",
    "ResendingStrategyResult",
)


#: Type specification for polling strategies
PollingStrategy = Callable[[bytes, bytes], float]

#: Type specification for result objects of a resending strategy
ResendingStrategyResult = Union[str, float]

#: Type specification for resending strategies
ResendingStrategy = Callable[[bool, bytes], ResendingStrategyResult]


class DefaultPollingStrategy:
    """Default polling strategy for Crazyflie connections.

    The polling strategy kicks in when we have no packets to send to the
    Crazyflie. Since the downstream link has to be pulled explicitly, we need
    to decide how much time to wait until we send a null packet to the
    Crazyflie.

    This function implements the default behaviour, which is as follows: send
    the next null packet immediately if we have received non-empty packets
    recently from the drone, otherwise poll the link at 100 Hz. We switch back
    to polling at 100 Hz if the last ten packets that we have received from
    the drone were empty.

    This polling strategy is consistent with the default behaviour of the
    Crazyflie radio protocol as implemented in the official Python API.
    """

    def __init__(self, frequency: float = 100):
        """Constructor.

        Parameters:
            frequency: the polling frequency if the link is idle
        """
        self._empty_packet_counter = 0
        self._is_idle = False
        self._interval = 1 / frequency

    def __call__(self, data: bytes, sent: bytes) -> float:
        """Returns the time to wait before we send the next null packet.

        Parameters:
            data: the last packet that we have received from the drone
            sent: the last packet that we have sent to the drone

        Returns:
            the proposed time to wait before sending the next null packet,
            in seconds; negative numbers mean to wait indefinitely
        """
        # The packet starts with the header so its length is always at least
        # 1. The packet is effectively empty if its length is less than 2.
        if len(sent) > 1 or len(data) > 1:
            self._empty_packet_counter = 0
            self._is_idle = False
        elif not self._is_idle:
            self._empty_packet_counter += 1
            if self._empty_packet_counter >= 10:
                self._is_idle = True
        return self._interval if self._is_idle else 0


class NoPollingStrategy:
    """Dummy polling strategy that never sends null packets to pull the
    downlink; it is assumed that we expect responses to our own packets only.
    """

    def __call__(self, received: bytes, sent: bytes) -> float:
        """Returns the time to wait before we send the next null packet.

        Parameters:
            data: the last packet that we have received from the drone
            sent: the last packet that we have sent to the drone

        Returns:
            -1 because it means "wait indefinitely" for the caller.
        """
        return -1


class BackoffPollingStrategy:
    """Lenient polling strategy for Crazyflie connections that gradually
    increases the time spent between consecutive null packets if there is no
    traffic to avoid saturating the link if there are many drones to communicate
    with.

    The strategy works as follows. If the last packet that we have received from
    the drone was non-empty, we poll the next packet immediately in case there
    is a continuation. Otherwise, we start polling at 100 Hz and double the
    delay after each polling attempt that yielded no response from the drone. If
    the delay reaches 250 msec, we do not increase it any further.
    """

    def __init__(self, initial_delay: float = 0.01, max_delay: float = 0.25):
        """Constructor.

        Parameters:
            initial_delay: the initial delay between consecutive polls, in
                seconds
            max_delay: maximum delay between consecutive polls.
        """
        self._empty_packet_counter = 0
        self._is_idle = False

        self._initial_delay = float(initial_delay)
        self._max_delay = float(max_delay)
        self._delay = self._initial_delay

    def __call__(self, data: bytes, sent: bytes) -> float:
        """Returns the time to wait before we send the next null packet.

        Parameters:
            data: the last packet that we have received from the drone
            sent: the last packet that we have sent to the drone

        Returns:
            the proposed time to wait before sending the next null packet, in
            seconds; negative numbers mean to wait indefinitely
        """
        # The packet starts with the header so its length is always at least
        # 1. The packet is effectively empty if its length is less than 2.
        if len(sent) > 1 or len(data) > 1:
            self._empty_packet_counter = 0
            self._is_idle = False
        elif not self._is_idle:
            self._empty_packet_counter += 1
            if self._empty_packet_counter >= 10:
                self._is_idle = True
                self._delay = self._initial_delay

        if self._is_idle:
            result = self._delay
            if self._delay < self._max_delay:
                self._delay = min(self._delay * 2, self._max_delay)
            return result
        else:
            return 0


class DefaultResendingStrategy:
    """Default packet resending strategy for Crazyflie connections.

    Radio packets to the Crazyflie occasionally fail to go through. This may be
    due to interference or due to the fact that the Crazyflie is not in the
    radio range any more. To distinguish between the two, this strategy resends
    failed packets and counts the number of consecutive failures; a link error
    is reported if the number of consecutive failures reaches a configurable
    threshold (100 attempts by default).
    """

    def __init__(self, attempts: int = 100):
        """Constructor.

        Parameters:
            attempts: number of sending attempts before giving up
        """
        self._remaining = self._attempts = 100

    def __call__(self, ack: bool, data: bytes) -> ResendingStrategyResult:
        """Decides whether the packet should be resent.

        Parameters:
            ack: whether the packet was acknowledged by the radio
            data: the packet that was sent

        Returns:
            whether the packet should be accepted (`accept`), re-sent again
            after a delay (returns the desired delay in seconds in this case),
            or the connection should be dropped (`stop`)
        """
        if ack:
            self._remaining = self._attempts
            return "accept"
        else:
            self._remaining -= 1
            return 0 if self._remaining > 0 else "stop"


class PatientResendingStrategy:
    """More patient packet resending strategy for Crazyflie connections."""

    _remaining: int
    _attempts: int
    _num_errors: int
    _scheduled: List[float]

    def __init__(self, attempts: int = 50):
        """Constructor.

        Parameters:
            attempts: number of sending attempts before giving up
        """
        self._remaining = self._attempts = 50
        self._num_errors = 0
        self._schedule = [0, 0, 0, 0, 0, 0, 0.01, 0.01, 0.01, 0.01, 0.01, 0.02]

    def __call__(self, ack: bool, data: bytes) -> ResendingStrategyResult:
        """Decides whether the packet should be resent.

        Parameters:
            ack: whether the packet was acknowledged by the radio
            data: the packet that was sent

        Returns:
            whether the packet should be accepted (`accept`), re-sent again
            after a delay (returns the desired delay in seconds in this case),
            or the connection should be dropped (`stop`)
        """
        if ack:
            self._remaining = self._attempts
            self._num_errors = 0
            return "accept"
        else:
            self._remaining -= 1
            self._num_errors += 1
            if self._remaining > 0:
                if len(self._schedule) > self._num_errors:
                    return self._schedule[self._num_errors]
                else:
                    return self._schedule[-1]
            else:
                return "stop"
