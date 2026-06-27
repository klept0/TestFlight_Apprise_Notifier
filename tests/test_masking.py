"""Tests for utils.masking.mask_secret."""

from utils.masking import mask_secret


def test_mask_discord_url():
    raw = "discord://123456789012/abcdEFGHijklMNOPqrstUVWX"
    masked = mask_secret(raw)
    assert masked.startswith("discord://")
    assert masked != raw
    # The webhook id and token body must not be exposed.
    assert "123456789012" not in masked
    assert "abcdEFGHijklMNOP" not in masked


def test_mask_telegram_url():
    raw = "tgram://1234567890:ABCdefGhIjkLmNoToken/987654321"
    masked = mask_secret(raw)
    assert masked.startswith("tgram://")
    assert masked != raw
    assert "ABCdefGhIjkLmNoToken" not in masked


def test_mask_slack_url():
    raw = "slack://TokenA000/TokenB000/TokenC000Secret/channel"
    masked = mask_secret(raw)
    assert masked.startswith("slack://")
    assert masked != raw
    assert "TokenA000" not in masked
    assert "TokenC000Secret" not in masked


def test_mask_matrix_url():
    raw = "matrix://user:Pass0rdSecret@matrix.example.org/!room"
    masked = mask_secret(raw)
    assert masked.startswith("matrix://")
    assert masked != raw
    assert "Pass0rdSecret" not in masked
    assert "matrix.example.org" not in masked


def test_mask_generic_webhook_url():
    raw = "https://hooks.example.com/services/T000/B000/Xyz0123456789Secret"
    masked = mask_secret(raw)
    assert masked.startswith("https://")
    assert masked != raw
    # Host and token body are hidden.
    assert "hooks.example.com" not in masked
    assert "Xyz0123456789Secret" not in masked


def test_plain_text_unchanged():
    text = "Beta is now OPEN for new testers"
    assert mask_secret(text) == text


def test_non_string_unchanged():
    assert mask_secret(None) is None
