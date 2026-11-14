import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _split_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.replace("\n", ",").split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    ai_provider: str = os.getenv("AI_PROVIDER", "groq").strip().lower()
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    groq_reasoning_effort: str = os.getenv("GROQ_REASONING_EFFORT", "low").strip()
    groq_max_completion_tokens: int = int(os.getenv("GROQ_MAX_COMPLETION_TOKENS", "9000"))
    output_dir: Path = PROJECT_ROOT / os.getenv("OUTPUT_DIR", "output")
    brand_name: str = os.getenv("BRAND_NAME", "The Daily Chronicle")
    cloudflare_account_id: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    cloudflare_api_token: str = os.getenv("CLOUDFLARE_API_TOKEN", "")
    cloudflare_model: str = os.getenv(
        "CLOUDFLARE_MODEL", "@cf/black-forest-labs/flux-1-schnell"
    )
    instagram_user_id: str = os.getenv("INSTAGRAM_USER_ID", "").strip()
    instagram_access_token: str = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    instagram_graph_api_version: str = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "").strip()
    instagram_public_media_base_url: str = os.getenv(
        "INSTAGRAM_PUBLIC_MEDIA_BASE_URL", ""
    ).strip()
    cloudinary_cloud_name: str = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
    cloudinary_api_key: str = os.getenv("CLOUDINARY_API_KEY", "").strip()
    cloudinary_api_secret: str = os.getenv("CLOUDINARY_API_SECRET", "").strip()

    @property
    def groq_api_keys(self) -> tuple[str, ...]:
        configured_keys = _split_values(os.getenv("GROQ_API_KEYS", ""))
        if configured_keys:
            return configured_keys
        return _split_values(self.groq_api_key)

    @property
    def cloudflare_credentials(self) -> tuple[tuple[str, str], ...]:
        account_ids = _split_values(os.getenv("CLOUDFLARE_ACCOUNT_IDS", ""))
        api_tokens = _split_values(os.getenv("CLOUDFLARE_API_TOKENS", ""))
        if not account_ids:
            account_ids = _split_values(self.cloudflare_account_id)
        if not api_tokens:
            api_tokens = _split_values(self.cloudflare_api_token)
        if account_ids or api_tokens:
            if not account_ids or len(account_ids) != len(api_tokens):
                raise ValueError(
                    "Cloudflare account ID and API token lists must both be configured "
                    "and contain the same number of comma-separated values."
                )
            return tuple(zip(account_ids, api_tokens))
        return ()

    @property
    def cloudinary_settings(self) -> tuple[str, str, str] | None:
        values = (
            self.cloudinary_cloud_name,
            self.cloudinary_api_key,
            self.cloudinary_api_secret,
        )
        if not any(values):
            return None
        if any(not value for value in values):
            raise ValueError(
                "Cloudinary hosting requires CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
            )
        return values


settings = Settings()
