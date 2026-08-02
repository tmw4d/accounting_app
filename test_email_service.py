import unittest
import email_service


class TestEmailService(unittest.TestCase):
    def test_email_validation_valid_cases(self):
        valid_emails = [
            "user@example.com",
            "treasurer@yula-ulti.org",
            "john.doe+reports@subdomain.domain.org",
            "test_123.abc@company.co.uk",
        ]
        for email in valid_emails:
            is_valid, msg = email_service.validate_email(email)
            self.assertTrue(is_valid, f"Expected valid for '{email}', got error: {msg}")
            self.assertEqual(msg, "")

    def test_email_validation_invalid_cases(self):
        invalid_emails = [
            "",
            "   ",
            "plainaddress",
            "@missinguser.com",
            "user@",
            "user@domain",
            "user@.com",
            "user@domain..com",
            "user space@domain.com",
            "user@domain .com",
        ]
        for email in invalid_emails:
            is_valid, msg = email_service.validate_email(email)
            self.assertFalse(is_valid, f"Expected invalid for '{email}'")
            self.assertTrue(len(msg) > 0, f"Expected error message for '{email}'")

    def test_ses_credentials(self):
        print("\n--- Testing AWS SES Capability ---")
        secrets = {}
        try:
            import toml
            import pathlib
            secrets_path = pathlib.Path(".streamlit/secrets.toml")
            if secrets_path.exists():
                secrets = toml.loads(secrets_path.read_text())
        except Exception:
            pass

        res = email_service.verify_ses_capability(secrets=secrets)
        print(f"SES Capability Result: {res}")
        if res.get("verified_identities"):
            print(f"Verified identities in AWS SES: {res.get('verified_identities')}")

        sender = secrets.get("EMAIL_SENDER", "treasurer@yula-ulti.org")
        print(f"Testing send_report_email dry-run with sender: {sender}")
        # Send test call
        send_res = email_service.send_report_email(
            recipient_email="treasurer@yula-ulti.org",
            subject="Test Financial Report Email",
            body_text="This is a test email sent from the test script.",
            pdf_bytes=b"%PDF-1.4 dummy pdf content",
            filename="test_report.pdf",
            sender_email=sender,
            secrets=secrets,
        )
        print(f"Send Email Result: {send_res}")


if __name__ == "__main__":
    unittest.main()
