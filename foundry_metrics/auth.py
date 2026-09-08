"""Azure Authentication helper for Azure AI Foundry and ARM APIs."""

import os
from typing import Optional, Tuple
from azure.core.credentials import TokenCredential


class AzureAuthManager:
    """Handles acquiring Azure credentials and tokens."""

    def __init__(self, use_device_code: bool = False, tenant_id: Optional[str] = None):
        self.use_device_code = use_device_code
        self.tenant_id = tenant_id or os.environ.get("AZURE_TENANT_ID")
        self._credential: Optional[TokenCredential] = None

    def get_credential(self) -> TokenCredential:
        """Acquire an Azure TokenCredential based on configuration and environment."""
        if self._credential:
            return self._credential

        if self.use_device_code:
            from azure.identity import DeviceCodeCredential
            self._credential = DeviceCodeCredential(tenant_id=self.tenant_id)
            return self._credential

        from azure.identity import DefaultAzureCredential
        # DefaultAzureCredential tries:
        # 1. EnvironmentCredential (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID)
        # 2. WorkloadIdentityCredential
        # 3. ManagedIdentityCredential
        # 4. AzureCliCredential
        # 5. AzureDeveloperCliCredential
        self._credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
        )
        return self._credential

    def get_token(self, scope: str = "https://management.azure.com/.default") -> str:
        """Get an access token string."""
        cred = self.get_credential()
        token = cred.get_token(scope)
        return token.token

    def verify_auth(self) -> Tuple[bool, Optional[str]]:
        """Verify that credentials are valid and can obtain a management token.

        Returns (is_valid, error_message).
        """
        try:
            self.get_token()
            return True, None
        except Exception as exc:
            return False, str(exc)


def get_auth_troubleshooting_guide() -> str:
    """Returns friendly guidance for Azure login."""
    return """
[bold red]Azure Authentication Required[/bold red]

The CLI needs access to your Azure subscription to query Azure Monitor metrics and Cost Management.
Choose one of the following methods to authenticate:

[bold cyan]1. Service Principal (Recommended for CI/CD & automation):[/bold cyan]
   Set these environment variables in your terminal or .env file:
   [green]export AZURE_TENANT_ID="<your-tenant-id>"[/green]
   [green]export AZURE_CLIENT_ID="<your-client-id>"[/green]
   [green]export AZURE_CLIENT_SECRET="<your-client-secret>"[/green]
   [green]export AZURE_SUBSCRIPTION_ID="<your-subscription-id>"[/green]

[bold cyan]2. Interactive Device Code Login:[/bold cyan]
   Run the CLI with the [yellow]--device-code[/yellow] flag to log in via your browser:
   [green]azure-ai-metrics --device-code[/green]

[bold cyan]3. Azure CLI (if az is installed):[/bold cyan]
   Log in with:
   [green]az login[/green]

[bold cyan]4. Quick Demo Mode (Offline / Preview):[/bold cyan]
   You can explore the tool and sample reports immediately without Azure credentials:
   [green]azure-ai-metrics --demo[/green]
"""
