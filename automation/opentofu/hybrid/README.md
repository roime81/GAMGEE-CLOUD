# GAMGEE Hybrid Cloud workspace

This starter workspace validates OpenTofu availability without creating resources.

Before provisioning external resources:
1. Create a dedicated workspace per cloud account and environment.
2. Add only the required provider and pin its version.
3. Use workload identity or short-lived credentials; do not commit credentials.
4. Store remote state in an encrypted, access-controlled backend with locking.
5. Require a reviewed plan and explicit approval before apply.
6. Import existing resources before attempting to manage them.
