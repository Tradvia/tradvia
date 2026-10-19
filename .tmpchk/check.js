const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const src = html.split("<script>")[1].split("</script>")[0];
new Function(src.replace(/^refresh\(\);$/m, "").replace(/document\.getElementById\("(refresh|lang|show-skipped)"\)\.addEventListener[^\n]*/g, ""));
const pure = src.split("/* ---------- Data layer")[0];
const api = new Function(pure + "; return {parseCost, classifyReply, buildTimeline, totalAndMedianCost};")();
console.log(api.parseCost("Estimated cost (API-equivalent): $0.0123 · 45s"), api.parseCost("Cost: $1.5 · 3s"),
  api.parseCost("Card x · estimated cost so far (API-equivalent): $0.2"), api.parseCost("nada"));
console.log(["Aprovado!", "não", "Rejeitado", "muda X", "LGTM"].map(api.classifyReply));
console.log(JSON.stringify(api.buildTimeline([
  { event: "commented", created_at: "2026-01-01T00:02:00Z", user: { login: "bob", type: "User" }, body: "ok" },
  { event: "commented", created_at: "2026-01-01T00:01:00Z", user: { login: "github-actions[bot]" },
    body: "🟡 **c**\nres\n1. a\n❓ q\nResponda **Aprovado**, x", html_url: "https://github.com/a/b/issues/1#c" },
  { event: "labeled", created_at: "2026-01-01T00:00:00Z", label: { name: "agent:ready" }, actor: { login: "al" } },
])));
