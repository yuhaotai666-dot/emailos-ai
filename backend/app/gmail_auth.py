"""One-time Gmail OAuth (read-only).

Usage:
    cd backend && source .venv/bin/activate
    python -m app.gmail_auth

Opens the browser for consent (log in with the test-user account), then saves
a refresh token to secrets/gmail_token.json. The scope is gmail.readonly —
the token cannot send, modify, or delete mail.
"""
from __future__ import annotations

from pathlib import Path

from .config import get_settings
from .services.gmail_provider import SCOPES


def main() -> None:
    settings = get_settings()
    creds_path = Path(settings.gmail_credentials_path)
    token_path = Path(settings.gmail_token_path)

    if not creds_path.exists():
        raise SystemExit(
            f"未找到 OAuth 凭证: {creds_path}\n"
            "请从 Google Cloud Console 下载 OAuth client JSON（Desktop app）并放到该路径。"
        )

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"✅ 授权成功，token 已保存: {token_path}")
    print("   在 .env 里设置 EMAIL_PROVIDER=gmail 并重启后端即可读取真实邮件（只读）。")


if __name__ == "__main__":
    main()
