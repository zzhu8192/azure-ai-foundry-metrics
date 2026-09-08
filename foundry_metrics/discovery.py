"""Discovery of Azure Subscriptions, AI Foundry / OpenAI resources, and model deployments."""

import os
from typing import List, Optional
import requests
from azure.core.credentials import TokenCredential
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient

from foundry_metrics.models import DeploymentInfo, ResourceInfo


class ResourceDiscovery:
    """Discovers AI resources and model deployments across Azure subscriptions."""

    def __init__(self, credential: TokenCredential):
        self.credential = credential

    def get_subscriptions(self, target_sub_id: Optional[str] = None) -> List[str]:
        """List target subscription IDs."""
        if target_sub_id:
            return [target_sub_id.strip()]

        env_sub = os.environ.get("AZURE_SUBSCRIPTION_ID")
        if env_sub:
            return [env_sub.strip()]

        # Query all accessible subscriptions via ARM REST API
        token = self.credential.get_token("https://management.azure.com/.default").token
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = "https://management.azure.com/subscriptions?api-version=2020-01-01"
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.ok:
                data = resp.json()
                sub_ids = [s["subscriptionId"] for s in data.get("value", []) if s.get("subscriptionId")]
                if sub_ids:
                    return sub_ids
        except Exception as exc:
            pass

        if env_sub:
            return [env_sub]
        raise RuntimeError("No Azure subscriptions found. Please set AZURE_SUBSCRIPTION_ID or login.")

    def discover_resources(
        self,
        subscription_ids: Optional[List[str]] = None,
        target_rg: Optional[str] = None,
        target_name: Optional[str] = None,
    ) -> List[ResourceInfo]:
        """Find all Azure AI Foundry, Azure OpenAI, and Cognitive Services resources."""
        subs = subscription_ids or self.get_subscriptions()
        results: List[ResourceInfo] = []

        for sub_id in subs:
            cog_client = CognitiveServicesManagementClient(self.credential, sub_id)
            try:
                accounts = list(cog_client.accounts.list())
            except Exception as exc:
                print(f"Warning: Could not list Cognitive Services in subscription {sub_id}: {exc}")
                continue

            for acc in accounts:
                if not acc.id:
                    continue

                # Filter by name if specified
                if target_name and acc.name and target_name.lower() not in acc.name.lower():
                    continue

                # Filter by resource group if specified
                parts = acc.id.split("/")
                rg = ""
                for i, part in enumerate(parts):
                    if part.lower() == "resourcegroups" and i + 1 < len(parts):
                        rg = parts[i + 1]
                        break

                if target_rg and rg.lower() != target_rg.lower():
                    continue

                # Check kind (OpenAI, AIServices, CognitiveServices)
                kind = acc.kind or "OpenAI"
                if kind.lower() not in ("openai", "aiservices", "cognitiveservices"):
                    continue

                # Retrieve deployments for this account
                deployments: List[DeploymentInfo] = []
                try:
                    deps = list(cog_client.deployments.list(rg, acc.name))
                    for d in deps:
                        props = getattr(d, "properties", None)
                        model_name = ""
                        model_ver = None
                        capacity = None
                        if props and getattr(props, "model", None):
                            model_obj = props.model
                            model_name = getattr(model_obj, "name", "") or ""
                            model_ver = getattr(model_obj, "version", None)

                        sku_name = d.sku.name if getattr(d, "sku", None) else None
                        if getattr(d, "sku", None) and getattr(d.sku, "capacity", None):
                            capacity = d.sku.capacity

                        deployments.append(
                            DeploymentInfo(
                                name=d.name or "",
                                model_name=model_name or d.name or "",
                                model_version=model_ver,
                                sku_name=sku_name,
                                capacity=capacity,
                            )
                        )
                except Exception as exc:
                    print(f"Notice: Deployments API unavailable for {acc.name} ({exc}). Using Monitor metrics directly.")

                results.append(
                    ResourceInfo(
                        id=acc.id,
                        name=acc.name or "",
                        resource_group=rg,
                        subscription_id=sub_id,
                        kind=kind,
                        location=acc.location or "",
                        deployments=deployments,
                    )
                )

        return results
