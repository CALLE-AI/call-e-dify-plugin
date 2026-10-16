from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from utils.client import CalleApiError, CalleClient, normalize_base_url


class CallEProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        api_key = str(credentials.get("api_key") or "").strip()
        if not api_key:
            raise ToolProviderCredentialValidationError("CALL-E API key is required.")

        try:
            base_url = normalize_base_url(credentials.get("base_url"))
            CalleClient(api_key=api_key, base_url=base_url).validate_credentials()
        except CalleApiError as error:
            raise ToolProviderCredentialValidationError(str(error)) from error
