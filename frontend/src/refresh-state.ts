// Refreshes replace DOM nodes. Match the saved control by its semantic owner,
// then its identity and duplicate occurrence, rather than its old node position.
type Mark = { key: string; occurrence: number };
export type RefreshState = { focus: Mark | null; opened: Mark[] };
const controls = 'a[href], button, input, select, textarea, summary, [tabindex], [id]';

function recordName(record: Element): string {
  return record.getAttribute('data-record') ?? record.querySelector(':scope > summary')?.textContent?.trim() ?? '';
}

function scope(root: HTMLElement, element: Element): string[] {
  const names: string[] = [];
  for (let parent = element.parentElement; parent && parent !== root; parent = parent.parentElement) {
    const owner = parent.getAttribute('data-focus-scope');
    if (owner !== null) names.unshift(`owner:${owner}`);
    if (parent.tagName === 'DETAILS') names.unshift(`record:${recordName(parent)}`);
  }
  return names;
}

function controlKey(root: HTMLElement, element: HTMLElement): string {
  const identity = element.id ? ['id', element.id]
    : element.dataset.action ? ['action', element.dataset.action, element.dataset.id ?? '']
    : element instanceof HTMLAnchorElement ? ['link', element.getAttribute('href'), element.textContent?.trim()]
    : [element.tagName, element.getAttribute('name'), element.textContent?.trim()];
  return JSON.stringify([scope(root, element), identity]);
}

function recordKey(root: HTMLElement, element: HTMLDetailsElement): string {
  return JSON.stringify([scope(root, element), recordName(element)]);
}

// Repeated links can share a URL and label. An event/section scope keeps them
// distinct, and an occurrence distinguishes duplicates inside that same owner.
export function semanticOccurrence(keys: string[], index: number): Mark | null {
  const key = keys[index];
  return key === undefined ? null : { key, occurrence: keys.slice(0, index).filter(candidate => candidate === key).length };
}

export function semanticIndex(keys: string[], mark: Mark): number {
  let occurrence = 0;
  return keys.findIndex(key => key === mark.key && occurrence++ === mark.occurrence);
}

function focusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll(controls)).filter((element): element is HTMLElement => element instanceof HTMLElement);
}

export function captureRefreshState(root: HTMLElement): RefreshState {
  const candidates = focusable(root);
  const records = Array.from(root.querySelectorAll<HTMLDetailsElement>('details'));
  const keys = records.map(element => recordKey(root, element));
  return {
    focus: semanticOccurrence(candidates.map(element => controlKey(root, element)), candidates.findIndex(element => element === document.activeElement)),
    opened: records.flatMap((element, index) => {
      const mark = element.open ? semanticOccurrence(keys, index) : null;
      return mark ? [mark] : [];
    }),
  };
}

export function restoreRefreshState(root: HTMLElement, state: RefreshState): boolean {
  const records = Array.from(root.querySelectorAll<HTMLDetailsElement>('details'));
  const recordKeys = records.map(element => recordKey(root, element));
  for (const mark of state.opened) {
    const record = records[semanticIndex(recordKeys, mark)];
    if (record) record.open = true;
  }
  if (!state.focus) return false;
  const candidates = focusable(root);
  const replacement = candidates[semanticIndex(candidates.map(element => controlKey(root, element)), state.focus)];
  if (!replacement) return false;
  replacement.focus({ preventScroll: true });
  return document.activeElement === replacement;
}
