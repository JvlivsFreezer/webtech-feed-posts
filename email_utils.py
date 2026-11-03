import logging
from email.message import EmailMessage

import aiosmtplib
from fastapi.templating import Jinja2Templates

from config import settings

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")


async def send_email(
    to_email: str,
    subject: str,
    plain_text: str,
    html_content: str | None = None,
) -> None:
    # Console backend: print instead of sending, handy for local development
    if settings.mail_backend == "console":
        print(f"\n--- Email to {to_email}: {subject} ---\n{plain_text}\n--- End ---\n")
        return

    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to_email
    message["Subject"] = subject

    message.set_content(plain_text)

    if html_content:
        message.add_alternative(html_content, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=settings.mail_server,
        port=settings.mail_port,
        username=settings.mail_username or None,
        password=settings.mail_password.get_secret_value() or None,
        start_tls=settings.mail_use_tls,
    )


async def _send_in_background(to_email: str, subject: str, **kwargs) -> None:
    # Runs as a BackgroundTask: log failures instead of crashing the request
    try:
        await send_email(to_email=to_email, subject=subject, **kwargs)
    except Exception:
        logger.exception("Failed to send email %r to %s", subject, to_email)


async def send_password_reset_email(to_email: str, username: str, token: str) -> None:
    reset_url = f"{settings.frontend_url}/reset-password?token={token}"

    template = templates.env.get_template("email/password_reset.html")
    html_content = template.render(reset_url=reset_url, username=username)

    plain_text = f"""Hi {username},

You requested to reset your password. Click the link below to set a new password:

{reset_url}

This link will expire in 1 hour.

If you didn't request this, you can safely ignore this email.

Best regards,
The {settings.site_name} Team
"""

    await _send_in_background(
        to_email,
        f"Reset Your Password - {settings.site_name}",
        plain_text=plain_text,
        html_content=html_content,
    )


async def send_verification_email(to_email: str, username: str, token: str) -> None:
    verify_url = f"{settings.frontend_url}/verify-email?token={token}"
    expire_hours = settings.verification_token_expire_hours

    template = templates.env.get_template("email/verify_email.html")
    html_content = template.render(
        verify_url=verify_url,
        username=username,
        expire_hours=expire_hours,
    )

    plain_text = f"""Hi {username},

Thanks for signing up! Please confirm your email address by visiting:

{verify_url}

This link will expire in {expire_hours} hours.

If you didn't create an account, you can safely ignore this email.

Best regards,
The {settings.site_name} Team
"""

    await _send_in_background(
        to_email,
        f"Verify Your Email - {settings.site_name}",
        plain_text=plain_text,
        html_content=html_content,
    )
