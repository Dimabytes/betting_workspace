from streamlit.components.v2 import component

_HTML = """
<span class="fv" data-state="no_data" role="status" aria-live="polite">
  <span class="fv-dot" aria-hidden="true"></span>
  <span class="fv-body">
    <span class="fv-label"></span>
    <span class="fv-note"></span>
  </span>
</span>
"""

_CSS = """
.fv {
  display: inline-flex;
  align-items: baseline;
  gap: 0.45rem;
  font-variant-numeric: tabular-nums;
  font-size: 0.95rem;
  color: #E6EAEE;
}
.fv-dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  flex: none;
  transform: translateY(-0.05rem);
  background: #8B98A5;
}
.fv-note { color: #8B98A5; margin-left: 0.4rem; font-size: 0.85rem; }
.fv[data-state="fresh"] .fv-dot { background: #4F9D7E; }
.fv[data-state="updating"] .fv-dot {
  background: #8B98A5;
  animation: fv-pulse 1.2s ease-in-out infinite;
}
.fv[data-state="stale"] .fv-dot { background: #8B98A5; }
.fv[data-state="stale"] .fv-label {
  color: #8B98A5;
  text-decoration: underline dashed;
  text-underline-offset: 0.18rem;
}
.fv[data-state="no_data"] .fv-label { color: #8B98A5; }
@keyframes fv-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}
@media (prefers-reduced-motion: reduce) {
  .fv[data-state="updating"] .fv-dot { animation: none; }
}
"""

_JS = """
export default function(component) {
  const { data, parentElement } = component;
  const root = parentElement.querySelector(".fv");
  if (!root || !data) return;
  const label = root.querySelector(".fv-label");
  const note = root.querySelector(".fv-note");
  const state = String(data.state || "no_data");
  const text = String(data.label || "— · нет данных");
  const noteText = data.note ? String(data.note) : "";
  if (root.dataset.state !== state) root.dataset.state = state;
  if (label.textContent !== text) label.textContent = text;
  if (note.textContent !== noteText) note.textContent = noteText;
}
"""

_fresh = component("trader_fresh", html=_HTML, css=_CSS, js=_JS, isolate_styles=True)


def render_fresh(key: str, label: str, state: str, note: str | None) -> None:
    _fresh(
        key=f"fresh-{key}",
        data={"label": label, "state": state, "note": note or ""},
        height="content",
    )
