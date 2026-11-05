// Pure filter helpers of the Transactions tab (tested in
// transactions_filter.test.cjs).

export interface TransactionsFilter {
  // YYYY-MM-DD, inclusive; "" = open bound.
  start: string;
  end: string;
  // null = all transactions, "" = no posting has a categorizer.
  categorizer: string | null;
  search: string;
  // Transactions with these narrations are left out.
  excludedNarrations: string[];
}

export type DatePreset =
  | "thisMonth"
  | "lastMonth"
  | "sinceLastMonth"
  | "thisYear"
  | "all";

export const datePresets: [DatePreset, string][] = [
  ["thisMonth", "This month"],
  ["lastMonth", "Last month"],
  ["sinceLastMonth", "Since last month"],
  ["thisYear", "This year"],
  ["all", "All"]
];

const pad = (n: number) => (n < 10 ? "0" : "") + n;

/** YYYY-MM-DD in local time (toISOString would use UTC). */
export function localIsoDate(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate()
  )}`;
}

export function datePreset(
  name: DatePreset,
  today: Date
): { start: string; end: string } {
  const year = today.getFullYear();
  const month = today.getMonth();
  const end = localIsoDate(today);
  // Date normalizes month -1 to December of the previous year.
  const firstOfLastMonth = localIsoDate(new Date(year, month - 1, 1));
  switch (name) {
    case "thisMonth":
      return { start: localIsoDate(new Date(year, month, 1)), end };
    case "lastMonth":
      // Day 0 is the last day of the previous month.
      return {
        start: firstOfLastMonth,
        end: localIsoDate(new Date(year, month, 0))
      };
    case "sinceLastMonth":
      return { start: firstOfLastMonth, end };
    case "thisYear":
      return { start: localIsoDate(new Date(year, 0, 1)), end };
    case "all":
      return { start: "", end: "" };
  }
}

export function defaultFilter(today: Date): TransactionsFilter {
  return {
    ...datePreset("sinceLastMonth", today),
    categorizer: null,
    search: "",
    excludedNarrations: []
  };
}

export function activePreset(
  filter: TransactionsFilter,
  today: Date
): DatePreset | undefined {
  for (const [name] of datePresets) {
    const range = datePreset(name, today);
    if (range.start === filter.start && range.end === filter.end) {
      return name;
    }
  }
  return undefined;
}

// The filter as stored in localStorage. A date preset is kept by name, so
// "This month" still means this month after the month changes.
export interface StoredFilter extends TransactionsFilter {
  preset?: DatePreset;
}

export function storedFilter(
  filter: TransactionsFilter,
  today: Date
): StoredFilter {
  const preset = activePreset(filter, today);
  return preset ? { ...filter, preset } : filter;
}

/** The stored filter, or the default one if `value` is not a valid filter. */
export function restoreFilter(value: unknown, today: Date): TransactionsFilter {
  const v = value as StoredFilter | null;
  const isString = (x: unknown) => typeof x === "string";
  if (
    !v ||
    !isString(v.start) ||
    !isString(v.end) ||
    !isString(v.search) ||
    !(v.categorizer === null || isString(v.categorizer))
  ) {
    return defaultFilter(today);
  }
  const dates = datePresets.some(([name]) => name === v.preset)
    ? datePreset(v.preset!, today)
    : { start: v.start, end: v.end };
  // Absent in filters stored before narrations could be excluded.
  const excluded = v.excludedNarrations;
  const excludedNarrations =
    Array.isArray(excluded) && excluded.every(isString) ? excluded : [];
  return {
    ...dates,
    categorizer: v.categorizer,
    search: v.search,
    excludedNarrations
  };
}

export type FilterArgs = Record<string, string | string[]>;

/** Filter arguments as the server parses them; categorizer is omitted for All. */
export function filterArgs(filter: TransactionsFilter): FilterArgs {
  const args: FilterArgs = {
    start: filter.start,
    end: filter.end,
    search: filter.search
  };
  if (filter.excludedNarrations.length) {
    args.exclude_narrations = filter.excludedNarrations;
  }
  if (filter.categorizer !== null) {
    args.categorizer = filter.categorizer;
  }
  return args;
}

/** Body of POST /transactions. */
export function transactionsRequest(
  filter: TransactionsFilter,
  generation: number,
  offset: number,
  limit: number
) {
  return { generation, ...filterArgs(filter), offset, limit };
}

export type ReviewMove = "first" | "previous" | "next" | "last";

/**
 * Moves through the Transactions list available while editing its row
 * `offset` (null: the transaction is not in the list) of `count`.
 */
export function reviewMoves(
  offset: number | null,
  count: number
): Record<ReviewMove, boolean> {
  return {
    first: count > 0 && offset !== 0,
    previous: offset !== null && offset > 0,
    next: offset !== null && offset + 1 < count,
    last: count > 0 && offset !== count - 1
  };
}
