import json
import os
import re

from pydantic import BaseModel, ConfigDict, ValidationError


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provision_tenant: bool
    reason: str


def interpret_intent(prompt: str) -> IntentDecision:
    """Classify a request without allowing the model to generate Kubernetes resources."""
    model = os.getenv("GAMGEE_LLM_MODEL")
    if not model:
        if re.search(
            r"\b(tenant|workspace|environment|service|deploy|provision|create)\b",
            prompt,
            flags=re.IGNORECASE,
        ):
            return IntentDecision(
                provision_tenant=True,
                reason="Accepted by deterministic provisioning mode.",
            )
        return IntentDecision(
            provision_tenant=False,
            reason="The request does not describe a supported provisioning action.",
        )

    try:
        from litellm import completion

        response = completion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify whether the user requests creating or provisioning a "
                        "tenant. Return only JSON with boolean provision_tenant and a "
                        "short reason. Never generate configuration or additional actions."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not isinstance(content, str):
            raise ValueError("Model returned no classification.")
        return IntentDecision.model_validate_json(content)
    except (ValidationError, ValueError, IndexError, AttributeError, KeyError) as exc:
        raise ValueError("The configured model returned an invalid intent decision.") from exc
    except Exception as exc:
        raise RuntimeError("The configured intent model is unavailable.") from exc
