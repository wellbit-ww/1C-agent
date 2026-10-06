/** Подписи этапов воронки: первая буква и аббревиатура КП. */
const STAGE_KP_RE = /(?<![а-яёa-z])кп(?![а-яёa-z])/gi;

export function formatStageLabel(label: string): string {
  const text = (label ?? "").trim();
  if (!text) return text;
  let out = text;
  const first = out[0];
  if (first === first.toLowerCase() && first !== first.toUpperCase()) {
    out = first.toUpperCase() + out.slice(1);
  }
  return out.replace(STAGE_KP_RE, "КП");
}
