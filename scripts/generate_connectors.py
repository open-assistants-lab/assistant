"""Batch generate ConnectKit connector YAMLs from Nango providers.yaml."""
from pathlib import Path

import yaml

KNOWN_CLI = {
    "aircall", "algolia", "apify", "atlassian", "bamboohr", "bitbucket", "bitly",
    "braintree", "brex", "clickup", "close", "cloudflare", "contentful", "crowdin",
    "databricks", "digitalocean", "dropbox", "dynatrace", "everhour", "fastly",
    "figma", "firebase", "freshdesk", "gcloud", "gitea", "gong", "grafana",
    "greenhouse", "height", "helpscout", "hightouch", "incident-io", "intercom",
    "jamf", "launchdarkly", "linear", "mailchimp", "mattermost", "metabase",
    "mixpanel", "modal", "netsuite", "openai", "opsgenie", "outreach", "pagerduty",
    "personio", "pipedrive", "posthog", "postman", "productboard", "ramp", "resend",
    "retool", "rootly", "salesforce", "salesloft", "sentry", "shopify", "shortcut",
    "slack", "snowflake", "spotify", "square", "statuspage", "surveymonkey",
    "tableau", "tines", "todoist", "toggl", "trello", "twilio", "typeform", "vercel",
    "webflow", "whatsapp", "workable", "zapier", "zendesk", "zoom",
}

KNOWN_MCP = {
    "airtable", "algolia", "apify", "apollo", "asana", "ashby", "atlassian",
    "attio", "bamboohr", "basecamp", "bitbucket", "box", "braintree", "braze",
    "brex", "cal-com", "chargebee", "circleci", "clickup", "close", "cloudflare",
    "confluence", "contentful", "crowdstrike", "crowdin", "databricks", "datadog",
    "digitalocean", "discord", "docusign", "dropbox", "everhour", "figma",
    "firebase", "firecrawl", "freshdesk", "front", "gcloud", "gitea", "gong",
    "grafana", "harvest", "height", "helpscout", "heroku", "hightouch", "hubspot",
    "incident-io", "intercom", "jamf", "klaviyo", "launchdarkly", "linear",
    "looker", "loom", "mailchimp", "mattermost", "metabase", "mixpanel", "modal",
    "monday", "mongodb", "netlify", "netsuite", "notion", "okta", "openai",
    "opsgenie", "outreach", "pagerduty", "pandadoc", "paypal", "personio",
    "pipedrive", "posthog", "postman", "prisma", "productboard", "ramp",
    "recharge", "resend", "retool", "rootly", "salesforce", "salesloft", "sendgrid",
    "sentry", "shopify", "shortcut", "slack", "snowflake", "spotify", "square",
    "statuspage", "surveymonkey", "tableau", "tines", "todoist", "toggl", "trello",
    "twilio", "typeform", "vercel", "webflow", "whimsical", "workable", "zapier",
    "zendesk", "zoom",
}

class OrderedDumper(yaml.Dumper):
    pass

def _dict_representer(dumper, data):
    return dumper.represent_mapping('tag:yaml.org,2002:map', data.items(), flow_style=False)

OrderedDumper.add_representer(dict, _dict_representer)
OrderedDumper.add_representer(list, lambda dumper, data: dumper.represent_sequence('tag:yaml.org,2002:seq', data, flow_style=False))


def nango_to_connectkit(provider_name: str, data: dict) -> dict | None:
    try:
        display = data.get("display_name", provider_name.replace("-", " ").title())
        docs = data.get("docs", "")
        setup_guide = data.get("setup_guide_url", docs)
        auth_mode = data.get("auth_mode", "OAUTH2").upper()
        categories = data.get("categories", ["other"])
        category = categories[0] if categories else "other"

        auth: dict = {}
        if auth_mode in ("OAUTH2", "OAUTH2_CC", "TWO_STEP"):
            auth["type"] = "oauth2"
            oauth2 = {}
            auth_url = data.get("authorization_url", "")
            token_url = data.get("token_url", "")
            scopes = data.get("default_scopes", ["read"])
            extra_params = data.get("authorization_params", {})

            if auth_url and token_url:
                oauth2["authorize_url"] = str(auth_url)
                oauth2["token_url"] = str(token_url)
                oauth2["scopes"] = scopes
                extra = {}
                for k, v in extra_params.items():
                    if k not in ("response_type", "scope", "state", "grant_type"):
                        extra[k] = v
                if extra:
                    oauth2["extra_params"] = extra
                oauth2["token_env_var"] = f"{provider_name.upper()}_ACCESS_TOKEN"
                auth["oauth2"] = oauth2
            else:
                return None

        elif auth_mode in ("API_KEY", "APP_STORE", "APP"):
            auth["type"] = "api_key"
            auth["api_key"] = {"env_var": f"{provider_name.upper()}_API_KEY"}

        elif auth_mode == "BASIC":
            auth["type"] = "api_key"
            auth["api_key"] = {"env_var": f"{provider_name.upper()}_API_KEY"}

        else:
            auth["type"] = "oauth2"
            auth["oauth2"] = {
                "authorize_url": data.get("authorization_url", ""),
                "token_url": data.get("token_url", ""),
                "scopes": data.get("default_scopes", ["read"]),
                "token_env_var": f"{provider_name.upper()}_ACCESS_TOKEN",
            }

        # Required fields from credentials + connection_config
        required_fields = []
        nango_creds = data.get("credentials", {})
        for key, cred in nango_creds.items():
            if not isinstance(cred, dict):
                continue
            if cred.get("automated") or cred.get("hidden") or cred.get("default_value"):
                continue
            required_fields.append({
                "name": key.replace("_", "_"),
                "label": cred.get("title", key.replace("_", " ").title()),
                "placeholder": cred.get("example", ""),
                "input_type": "password" if cred.get("secret") else "text",
                "help_text": cred.get("description", ""),
                "optional": False,
            })

        conn_config = data.get("connection_config", {})
        for key, cfg in conn_config.items():
            if not isinstance(cfg, dict):
                continue
            required_fields.append({
                "name": key.lower(),
                "label": cfg.get("title", "Domain"),
                "placeholder": cfg.get("example", ""),
                "input_type": "url" if cfg.get("format") == "hostname" else "text",
                "help_text": cfg.get("description", ""),
                "optional": cfg.get("optional", False),
            })

        auth["required_fields"] = required_fields

        has_cli = provider_name in KNOWN_CLI
        has_mcp = provider_name in KNOWN_MCP

        tool_sources = []
        if has_cli:
            tool_sources.append({
                "type": "cli",
                "command": provider_name.replace("_", "-"),
                "install": "# See service docs",
            })
        if has_mcp or not has_cli:
            mcp_name = provider_name.replace("_", "-")
            tool_sources.append({
                "type": "mcp",
                "server_name": mcp_name,
                "command": f"{mcp_name}-mcp-server",
            })

        if not tool_sources:
            return None

        spec = {
            "name": provider_name.replace("_", "-"),
            "display": display,
            "icon": provider_name.split("-")[0][:8],
            "category": category,
            "version": "1.0",
            "description": display,
            "auth": auth,
            "tool_sources": tool_sources,
        }

        if setup_guide:
            spec["setup_guide_url"] = setup_guide

        return spec

    except Exception:
        return None


def main():
    providers = yaml.safe_load(open(
        "/Users/eddy/Developer/Langgraph/assistant/docs/reference/nango_providers.yaml"
    ))
    output_dir = Path(
        "/Users/eddy/Developer/Langgraph/assistant/packages/connectkit/connectors"
    )
    existing = {f.stem for f in output_dir.glob("*.yaml")}

    count = 0
    for name, data in sorted(providers.items()):
        safe_name = name.replace("_", "-")

        if safe_name in existing:
            continue

        # Skip variants
        skip = False
        for s in ("-scim", "-sandbox", "-beta", "-test", "-basic", "-pat", "-oauth",
                  "-gov", "-next-gen", "-mcp", "-lyric", "-run", "-admin", "-cc"):
            if s in safe_name:
                skip = True
                break
        if skip:
            continue

        spec = nango_to_connectkit(name, data)
        if spec is None:
            continue

        # Remove empty tool_sources key if single source
        if len(spec["tool_sources"]) == 1 and spec["tool_sources"][0]["type"] == "mcp":
            spec["tool_source"] = spec.pop("tool_sources")[0]
        elif len(spec["tool_sources"]) == 1:
            spec["tool_source"] = spec.pop("tool_sources")[0]

        out_path = output_dir / f"{safe_name}.yaml"
        with open(out_path, "w") as f:
            yaml.dump(spec, f, Dumper=OrderedDumper, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120)
        count += 1

    print(f"Generated {count} new connectors")


if __name__ == "__main__":
    main()
