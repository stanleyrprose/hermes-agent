        # Provider preferences (only, ignore, order, sort) are OpenRouter-
        # specific.  Only send to OpenRouter-compatible endpoints.
        # TODO: Nous Portal will add transparent proxy support — re-enable
        # for _is_nous when their backend is updated.
        if provider_preferences and _is_openrouter:
            extra_body["provider"] = provider_preferences
        _is_nous = "nousresearch" in self._base_url_lower

        if self._supports_reasoning_extra_body():
            if _is_github_models:
                github_reasoning = self._github_models_reasoning_extra_body()
                if github_reasoning is not None:
                    extra_body["reasoning"] = github_reasoning
            else:
                if self.reasoning_config is not None:
                    rc = dict(self.reasoning_config)
                    # Nous Portal requires reasoning enabled — don't send
                    # enabled=false to it (would cause 400).
                    if _is_nous and rc.get("enabled") is False:
                        pass  # omit reasoning entirely for Nous when disabled
                    else:
                        extra_body["reasoning"] = rc
                else:
                    extra_body["reasoning"] = {
                        "enabled": True,
                        "effort": "medium"
                    }

        # Nous Portal product attribution
        if _is_nous:
            extra_body["tags"] = ["product=hermes-agent"]

        # Ollama num_ctx: override the 2048 default so the model actually
        # uses the context window it was trained for.  Passed via the OpenAI
        # SDK's extra_body → options.num_ctx, which Ollama's OpenAI-compat
        # endpoint forwards to the runner as --ctx-size.
        if self._ollama_num_ctx:
            options = extra_body.get("options", {})
            options["num_ctx"] = self._ollama_num_ctx
            extra_body["options"] = options

        # Ollama / custom provider: pass think=false when reasoning is disabled.
        # Ollama does not recognise the OpenRouter-style `reasoning` extra_body
        # field, so we use its native `think` parameter instead.
        # This prevents thinking-capable models (Qwen3, etc.) from generating
        # <think> blocks and producing empty-response errors when the user has
        # set reasoning_effort: none.
        if self.provider == "custom" and self.reasoning_config and isinstance(self.reasoning_config, dict):
            _effort = (self.reasoning_config.get("effort") or "").strip().lower()
            _enabled = self.reasoning_config.get("enabled", True)
            if _effort == "none" or _enabled is False:
                extra_body["think"] = False

        # DeepSeek V4: disable thinking by default (flash = non-reasoning model).
        # V4 defaults to thinking mode; pass thinking=disabled so responses are direct.
        if self.provider == "deepseek" and self.model in ("deepseek-v4-flash", "deepseek-v4-pro"):
            extra_body["thinking"] = {"type": "disabled"}

        if self._is_qwen_portal():
            extra_body["vl_high_resolution_images"] = True

        if extra_body:
            api_kwargs["extra_body"] = extra_body

        # Priority Processing / generic request overrides (e.g. service_tier).
        # Applied last so overrides win over any defaults set above.
        if self.request_overrides:
            api_kwargs.update(self.request_overrides)

        return api_kwargs
