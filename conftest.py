"""
conftest.py — Pytest shared fixtures for SatyaLens analysis tests
"""
import sys
import os
import pytest

# Ensure the package root is on sys.path regardless of how pytest is invoked
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def sample_scam_text():
    """A realistic scam message hitting several rule categories."""
    return (
        "URGENT: I am from the bank. Your account will be blocked in 10 minutes. "
        "Act now! Share your OTP immediately. Don't tell anyone about this. "
        "Please pay ₹50,000 via a gift card or send to this new account. "
        "Install AnyDesk so I can help you right now."
    )


@pytest.fixture(scope="session")
def sample_clean_text():
    """A normal, non-suspicious message."""
    return (
        "Hi, the invoice for last month's project is attached. "
        "Please transfer ₹12,500 to the usual bank account by Friday. "
        "Let me know if you have any questions."
    )


@pytest.fixture(scope="session")
def expected_payment_fields():
    """Sample expected fields for payment reconciliation tests."""
    return {
        "amount": "5000",
        "transaction_id": "UTR123456789",
        "payee_name": "John Doe",
        "upi_id": "johndoe@okaxis",
        "status_text": "success",
    }
