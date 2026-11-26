# Deploying the demo

The public demo is one container on Azure Container Apps (Sweden Central), scaled to zero when idle,
with images in Azure Container Registry. Recorded runs and the saved eval reports ship inside the
image; live runs call Claude with the API key and access code held as Container Apps secrets.

## One-time setup

- A resource group with a Container Registry (Basic), a Container Apps environment, and the app,
  which pulls from the registry with its system-assigned identity (role `AcrPull`).
- Two app secrets, `anthropic-key` and `access-code`, mapped to `ANTHROPIC_API_KEY` and
  `DEMO_ACCESS_CODE`. The key belongs to an Anthropic workspace with its own spend limit.
- A deploy identity: a service principal with `Contributor` on that resource group only, so
  deploys don't depend on an interactive sign-in. Its secret lives in a password manager.

## Each release

```bash
az login --service-principal -u "$APP_ID" -p "$SECRET" --tenant "$TENANT"   # the deploy identity
TAG=$(git rev-parse --short HEAD)
docker build -t "$REGISTRY.azurecr.io/support-triage-agent:$TAG" .
az acr login -n "$REGISTRY"
docker push "$REGISTRY.azurecr.io/support-triage-agent:$TAG"
az containerapp update -g "$RESOURCE_GROUP" -n "$APP" --image "$REGISTRY.azurecr.io/support-triage-agent:$TAG"
```

The image is built locally because ACR Tasks aren't available on this subscription. Every image is
tagged with its commit, so rolling back is the same `update` with an earlier tag.

## After deploying

- `GET /healthz` answers, `GET /api/tickets` lists every sample ticket, `GET /api/evals` lists runs.
- Click through the tour: prompt injection, credit approved and archived, second credit blocked,
  escalation, Mark done.
- One live run with the access code returns a proposal and its cost; without the code it is refused.
