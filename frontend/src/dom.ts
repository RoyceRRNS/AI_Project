/** True when a keyboard event comes from a text field (so global shortcuts should stay quiet). */
export function isTyping(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest("input, textarea, [contenteditable]");
}
