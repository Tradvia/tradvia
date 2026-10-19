# Hello World: the first hybrid flow

The smallest end-to-end loop of Tradvia v0.1:

```
GitHub issue ──► @Agent-Architect plans (LiteLLM)
                     │
                     ▼
             question in Discord ──► card WAITING_HUMAN (run paused)
                     │
          you reply "Aprovado" / "Rejeitado" / a change request
                     │
                     ▼
             @Agent-Coder: branch ─► commit ─► pull request ──► card IN_REVIEW_PR
```

Statuses follow [`core/contracts/card-state.v1.json`](../core/contracts/card-state.v1.json).
The pause uses LangGraph `interrupt()`, so the run resumes exactly where it stopped.

## Setup (once)

1. **GitHub token**: Settings → Developer settings → Fine-grained tokens → Generate.
   Resource owner `Tradvia`, only repository `Tradvia/tradvia`, permissions
   *Contents*, *Pull requests* and *Issues*: Read and write.
2. **Discord bot**: [Developer Portal](https://discord.com/developers/applications) →
   New Application → Bot → Reset Token (copy it) → enable **Message Content Intent**.
   OAuth2 → URL Generator → scope `bot`, permissions *Send Messages* and
   *Read Message History* → open the URL and add the bot to your server.
3. **Channel ID**: Discord → User Settings → Advanced → Developer Mode on;
   right-click the channel → Copy Channel ID.
4. **Model key**: an API key for the provider in `TRADVIA_PLANNER_MODEL`
   (default Anthropic → `ANTHROPIC_API_KEY`).

```bash
cd core
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # fill in the values
```

## Run

1. Open an issue in `Tradvia/tradvia`, e.g. *"Add a hello world note"*.
2. From `core/`:

   ```bash
   tradvia --issue 1
   ```

3. Answer in Discord: **Aprovado** opens the PR, **Rejeitado** sends the card back
   to `BACKLOG`, anything else is treated as feedback and the Architect re-plans
   (up to 3 times).

Use `TRADVIA_OFFLINE=1` for a first run without any LLM call.

## Safety rails

- Agents never merge. The PR goes through the normal protected-branch review.
- Agents cannot write to `.github/`, `.git/`, `LICENSE.md`, `CLA.md` or `CODEOWNERS`,
  nor outside the repository.
- Status changes that break the card contract raise an error.

## Tests

```bash
cd core && pytest -q
```

GitHub, Discord and the model are replaced by fakes, so the tests need no tokens.
