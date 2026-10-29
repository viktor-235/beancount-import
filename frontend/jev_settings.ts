// JEV settings: regular expressions selecting the accounts offered to JEV as
// categories, the transactions JEV must not classify, the confidence from
// which a JEV answer counts as valid, and the prompt. Stored per browser
// in localStorage, like the other settings, and sent with each request.

export interface JevAccountFilters {
  include: string[];
  exclude: string[];
}

// Keep in sync with jev.DEFAULT_ACCOUNT_FILTERS.
export const DEFAULT_JEV_ACCOUNT_FILTERS: JevAccountFilters = {
  include: ["^(Expenses|Income):", "^(Assets|Liabilities):"],
  exclude: [
    "^Expenses:Unknown$",
    "^Income:Unknown$",
    "^Expenses:FIXME$",
    ":Unattributed",
  ],
};

const STORAGE_KEY = "jevAccountFilters";

export function parsePatterns(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

export function invalidPatterns(patterns: string[]): string[] {
  return patterns.filter((pattern) => {
    try {
      new RegExp(pattern);
      return false;
    } catch (_) {
      return true;
    }
  });
}

export function loadJevAccountFilters(): JevAccountFilters {
  try {
    const value = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "");
    if (
      Array.isArray(value.include) &&
      Array.isArray(value.exclude) &&
      [...value.include, ...value.exclude].every((p) => typeof p === "string")
    ) {
      return { include: value.include, exclude: value.exclude };
    }
  } catch (_) {
    /* missing, malformed or unavailable storage */
  }
  return DEFAULT_JEV_ACCOUNT_FILTERS;
}

export function saveJevAccountFilters(filters: JevAccountFilters | null) {
  try {
    if (filters === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
  } catch (_) {
    /* storage unavailable */
  }
}

const SKIP_STORAGE_KEY = "jevSkipTransactions";

// Fired on window when settings that affect rendered candidates change.
export const JEV_SETTINGS_EVENT = "jev-settings-change";

export function loadJevSkipPatterns(): string[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(SKIP_STORAGE_KEY) || "");
    if (Array.isArray(value) && value.every((p) => typeof p === "string"))
      return value;
  } catch (_) {
    /* missing, malformed or unavailable storage */
  }
  return [];
}

export function saveJevSkipPatterns(patterns: string[]) {
  try {
    window.localStorage.setItem(SKIP_STORAGE_KEY, JSON.stringify(patterns));
  } catch (_) {
    /* storage unavailable */
  }
  window.dispatchEvent(new Event(JEV_SETTINGS_EVENT));
}

// First pattern found in the transaction text JEV would receive (multiline:
// ^ and $ match at line boundaries). Invalid patterns never match here; the
// server rejects them.
export function jevSkipMatch(
  patterns: string[],
  transaction: string
): string | undefined {
  return patterns.find((pattern) => {
    try {
      return new RegExp(pattern, "m").test(transaction);
    } catch (_) {
      return false;
    }
  });
}

const VALID_CONFIDENCE_KEY = "jevValidConfidence";
export const DEFAULT_JEV_VALID_CONFIDENCE_PERCENT = 90;

export function parseConfidencePercent(text: string): number | undefined {
  const value = Number(text.trim());
  return text.trim() !== "" && value >= 0 && value <= 100 ? value : undefined;
}

export function loadJevValidConfidencePercent(): number {
  try {
    const value = parseConfidencePercent(
      window.localStorage.getItem(VALID_CONFIDENCE_KEY) || ""
    );
    if (value !== undefined) return value;
  } catch (_) {
    /* storage unavailable */
  }
  return DEFAULT_JEV_VALID_CONFIDENCE_PERCENT;
}

export function saveJevValidConfidencePercent(percent: number) {
  try {
    window.localStorage.setItem(VALID_CONFIDENCE_KEY, String(percent));
  } catch (_) {
    /* storage unavailable */
  }
  window.dispatchEvent(new Event(JEV_SETTINGS_EVENT));
}

// Whether a JEV answer is confident enough to be trusted (and, later,
// applied by batch classification).
export function isJevAnswerValid(confidence: number): boolean {
  // Compare in [0, 1]: 0.57 * 100 is 56.99999999999999 in floating point.
  return confidence >= loadJevValidConfidencePercent() / 100;
}

const INSTRUCTIONS_KEY = "jevInstructions";

// Custom JEV instructions, or null for the server default
// (jev.DEFAULT_INSTRUCTIONS, sent as jev_default_instructions).
export function loadJevInstructions(): string | null {
  try {
    const value = window.localStorage.getItem(INSTRUCTIONS_KEY);
    return value && value.trim() ? value : null;
  } catch (_) {
    return null;
  }
}

export function saveJevInstructions(instructions: string | null) {
  try {
    if (instructions === null || !instructions.trim())
      window.localStorage.removeItem(INSTRUCTIONS_KEY);
    else window.localStorage.setItem(INSTRUCTIONS_KEY, instructions);
  } catch (_) {
    /* storage unavailable */
  }
}
