import math
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from config import settings


class RateLimiter:
    """
    In-memory sliding-window limiter, used as a dependency:

        @router.post("/token", dependencies=[Depends(login_limiter)])

    Keyed by client IP. State lives in this process only, which is fine for a
    single server; use Redis or similar if you run multiple workers.
    """

    _instances: list["RateLimiter"] = []

    def __init__(self, times: int, seconds: int) -> None:
        self.times = times
        self.seconds = seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        RateLimiter._instances.append(self)

    async def __call__(self, request: Request) -> None:
        if not settings.rate_limit_enabled:
            return

        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        hits = self._hits[key]

        while hits and hits[0] <= now - self.seconds:
            hits.popleft()

        if len(hits) >= self.times:
            retry_after = math.ceil(hits[0] + self.seconds - now)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)

    @classmethod
    def reset_all(cls) -> None:
        for limiter in cls._instances:
            limiter._hits.clear()


login_limiter = RateLimiter(times=10, seconds=60)
register_limiter = RateLimiter(times=10, seconds=60 * 60)
password_email_limiter = RateLimiter(times=5, seconds=60 * 15)
