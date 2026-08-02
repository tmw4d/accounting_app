import os
import re
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple, Dict, Any, Optional


EMAIL_REGEX = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    re.IGNORECASE,
)


class EmailServiceError(Exception):
    """Custom exception for email service errors."""
    pass


def validate_email(email_str: str) -> Tuple[bool, str]:
    """
    Validates a free-form email address string against standard formatting rules.
    Returns (is_valid, error_message).
    """
    if not email_str or not isinstance(email_str, str):
        return False, "Email address cannot be empty."
    
    cleaned = email_str.strip()
    if not cleaned:
        return False, "Email address cannot be empty."
    
    if len(cleaned) > 254:
        return False, "Email address is too long (maximum 254 characters)."

    if not EMAIL_REGEX.match(cleaned):
        return False, "Invalid email address format (e.g., name@domain.com)."

    return True, ""


def get_aws_config(secrets=None) -> Dict[str, Any]:
    """
    Retrieves AWS credentials and SES sender email from provided secrets, Streamlit secrets, or environment variables.
    """
    config = {}

    if secrets:
        config["aws_access_key_id"] = secrets.get("AWS_ACCESS_KEY_ID")
        config["aws_secret_access_key"] = secrets.get("AWS_SECRET_ACCESS_KEY")
        config["region_name"] = secrets.get("AWS_DEFAULT_REGION") or secrets.get("AWS_REGION", "us-east-1")
        config["sender_email"] = secrets.get("EMAIL_SENDER")
    
    # Fallback to Streamlit secrets if missing
    if not config.get("aws_access_key_id"):
        try:
            import streamlit as st
            if hasattr(st, "secrets"):
                config["aws_access_key_id"] = config.get("aws_access_key_id") or st.secrets.get("AWS_ACCESS_KEY_ID")
                config["aws_secret_access_key"] = config.get("aws_secret_access_key") or st.secrets.get("AWS_SECRET_ACCESS_KEY")
                config["region_name"] = config.get("region_name") or st.secrets.get("AWS_DEFAULT_REGION", "us-east-1")
                config["sender_email"] = config.get("sender_email") or st.secrets.get("EMAIL_SENDER")
        except Exception:
            pass

    # Fallback to OS environment variables
    config["aws_access_key_id"] = config.get("aws_access_key_id") or os.getenv("AWS_ACCESS_KEY_ID")
    config["aws_secret_access_key"] = config.get("aws_secret_access_key") or os.getenv("AWS_SECRET_ACCESS_KEY")
    config["region_name"] = config.get("region_name") or os.getenv("AWS_DEFAULT_REGION", os.getenv("AWS_REGION", "us-east-1"))
    config["sender_email"] = config.get("sender_email") or os.getenv("EMAIL_SENDER")

    missing = []
    if not config.get("aws_access_key_id"):
        missing.append("AWS_ACCESS_KEY_ID")
    if not config.get("aws_secret_access_key"):
        missing.append("AWS_SECRET_ACCESS_KEY")

    if missing:
        raise EmailServiceError(f"Missing required AWS credentials: {', '.join(missing)}")

    return config


def get_ses_client(secrets=None):
    """
    Initializes and returns a boto3 SES client using resolved AWS credentials.
    """
    try:
        import boto3
    except ImportError as exc:
        raise EmailServiceError("boto3 is required for AWS SES integration. Install dependencies from requirements.txt.") from exc

    config = get_aws_config(secrets)
    return boto3.client(
        "ses",
        aws_access_key_id=config["aws_access_key_id"],
        aws_secret_access_key=config["aws_secret_access_key"],
        region_name=config["region_name"],
    )


def verify_ses_capability(secrets=None) -> Dict[str, Any]:
    """
    Checks if AWS credentials can communicate with SES and have permission to send emails or query quota.
    """
    try:
        client = get_ses_client(secrets)
        quota_info = {}
        try:
            quota = client.get_send_quota()
            quota_info = {
                "max_24_hour_send": quota.get("Max24HourSend", 0),
                "max_send_rate": quota.get("MaxSendRate", 0),
                "sent_last_24_hours": quota.get("SentLast24Hours", 0),
            }
        except Exception:
            pass

        try:
            identities = client.list_identities().get("Identities", [])
            quota_info["verified_identities"] = identities
        except Exception:
            pass

        return {
            "ok": True,
            **quota_info,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


def send_report_email(
    recipient_email: str,
    subject: str,
    body_text: str,
    pdf_bytes: bytes,
    filename: str = "report.pdf",
    sender_email: Optional[str] = None,
    secrets=None,
) -> Dict[str, Any]:
    """
    Sends an email with a PDF report attachment via AWS SES.
    Returns a dictionary with 'success' status and details/error message.
    """
    # 1. Validate recipient email format
    is_valid, val_err = validate_email(recipient_email)
    if not is_valid:
        return {"success": False, "error": f"Invalid recipient email: {val_err}"}

    cleaned_recipient = recipient_email.strip()

    # 2. Determine sender email
    config = {}
    try:
        config = get_aws_config(secrets)
    except EmailServiceError as err:
        return {"success": False, "error": str(err)}

    effective_sender = (sender_email or config.get("sender_email") or "").strip()
    if not effective_sender:
        return {"success": False, "error": "Sender email address is required (set EMAIL_SENDER in secrets or provide sender)."}

    is_sender_valid, sender_val_err = validate_email(effective_sender)
    if not is_sender_valid:
        return {"success": False, "error": f"Invalid sender email: {sender_val_err}"}

    # 3. Construct raw MIME message
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = effective_sender
    msg["To"] = cleaned_recipient

    # Text body
    text_part = MIMEText(body_text, "plain", "utf-8")
    msg.attach(text_part)

    # Attachment
    if pdf_bytes:
        att = MIMEApplication(pdf_bytes, _subtype="pdf")
        att.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(att)

    # 4. Send via boto3 SES
    try:
        client = get_ses_client(secrets)
        response = client.send_raw_email(
            Source=effective_sender,
            Destinations=[cleaned_recipient],
            RawMessage={"Data": msg.as_bytes()},
        )
        return {
            "success": True,
            "message_id": response.get("MessageId"),
            "sender": effective_sender,
            "recipient": cleaned_recipient,
        }
    except Exception as exc:
        err_msg = str(exc)

        # Enhance common AWS SES error messages for user clarity
        if "MessageRejected" in err_msg and "Email address is not verified" in err_msg:
            err_msg = (
                f"AWS SES Sandbox Restriction: Email address '{cleaned_recipient}' or '{effective_sender}' "
                "is not verified in AWS SES. In Sandbox mode, both sender and recipient must be verified."
            )
        elif "AccessDenied" in err_msg or "User is not authorized" in err_msg:
            err_msg = f"AWS SES Permission Denied: The AWS credentials do not have permission to execute ses:SendRawEmail. Details: {err_msg}"
        
        return {"success": False, "error": err_msg}
