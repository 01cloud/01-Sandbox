import os

import httpx


async def send_expiry_email(payload: dict) -> None:
    """
    Sends a transactional email notification to a user whose API key is approaching expiration.
    Uses SendGrid's Web API if SENDGRID_API_KEY is configured. Otherwise, logs the email details
    to stdout/stderr (Mock Mode) to simplify local testing and development.

    If the SendGrid API call fails (returns non-2xx status), it raises an exception, prompting
    RabbitMQ to handle delivery retries via DLQ and backoff mechanisms.
    """
    recipient = payload.get("recipient")
    key_name = payload.get("key_name")
    expires_at = payload.get("expires_at")
    prefix = payload.get("prefix")
    job_id = payload.get("job_id", "unknown")

    if not recipient:
        print(
            f"[Email Service] Error: No recipient email specified in payload for job {job_id}"
        )
        return

    subject = f"Security Alert: Your API key '{key_name}' is approaching expiration"
    body = (
        f"Hello,\n\n"
        f"This is an automated notification that your API key '{key_name}' "
        f"(prefix: {prefix}) is approaching expiration and will expire on {expires_at}.\n\n"
        f"To prevent any service interruption, please generate a new API key as soon as possible "
        f"and update your client configuration.\n\n"
        f"Best Regards,\n"
        f"01 Sandbox Security Team"
    )

    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("SENDGRID_FROM_EMAIL", "no-reply@01sandbox.com")

    if not sendgrid_key:
        print(
            "[Email Service] [MOCK MODE] SENDGRID_API_KEY is not set. Simulating email delivery:"
        )
        print(f"  Job ID: {job_id}")
        print(f"  To: {recipient}")
        print(f"  From: {from_email}")
        print(f"  Subject: {subject}")
        print(f"  Body:\n{body}")
        print("[Email Service] [MOCK MODE] Email sent successfully (logged).")
        return

    # Call SendGrid API v3
    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {sendgrid_key}",
        "Content-Type": "application/json",
    }

    data = {
        "personalizations": [{"to": [{"email": recipient}], "subject": subject}],
        "from": {"email": from_email},
        "content": [{"type": "text/plain", "value": body}],
    }

    print(
        f"[Email Service] Sending email via SendGrid for job={job_id[:8]} to={recipient}..."
    )
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=data, headers=headers, timeout=10.0)

    if response.status_code < 200 or response.status_code >= 300:
        error_msg = (
            f"SendGrid API failed with status {response.status_code}: {response.text}"
        )
        print(f"[Email Service] Error: {error_msg}")
        raise RuntimeError(error_msg)

    print(f"[Email Service] Email sent successfully via SendGrid for job={job_id[:8]}.")
