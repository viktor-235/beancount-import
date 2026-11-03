import * as React from "react";
import styled from "styled-components";
import { EventEmitter, EventSubscription } from "fbemitter";

import {
  FetchError,
  fetchJson,
  ReviewState,
  secretKey,
  ServerConnection,
  ServerListCache,
  ServerState,
  TransactionItem,
  TransactionsPage
} from "./server_connection";
import {
  ServerListMetadata,
  ServerVirtualListComponent,
  ServerVirtualListSource
} from "./server_virtual_list";
import { VirtualListScrollState } from "./virtual_list";
import { PendingEntryFormattedElement } from "./pending";
import {
  activePreset,
  datePreset,
  datePresets,
  DatePreset,
  filterQuery,
  restoreFilter,
  storedFilter,
  TransactionsFilter
} from "./transactions_filter";

const retryDelay = 1000;
const searchDelay = 300;

function fetchPage(
  filter: TransactionsFilter,
  journalGeneration: number,
  offset: number,
  limit: number
): Promise<TransactionsPage> {
  return fetchJson(
    `${window.location.pathname}${secretKey}/transactions?${filterQuery(
      filter,
      journalGeneration,
      offset,
      limit
    )}`
  );
}

const FILTER_STORAGE_KEY = "transactionsFilter";

/** The filter saved by the last setFilter, kept across page reloads. */
export function loadTransactionsFilter(): TransactionsFilter {
  try {
    return restoreFilter(
      JSON.parse(window.localStorage.getItem(FILTER_STORAGE_KEY) || "null"),
      new Date()
    );
  } catch (_) {
    return restoreFilter(null, new Date());
  }
}

/**
 * Filter and list of the Transactions tab. Owned by the App, so the filter
 * survives tab switches. Asks the server only while active (tab open) and
 * only when the journal generation or the filter changed.
 *
 * Emits "change" with new list metadata (for the virtual list) and "update"
 * when anything shown in the header changed.
 */
export class TransactionsListState
  implements ServerVirtualListSource<TransactionItem> {
  scrollState = new VirtualListScrollState();
  metadata?: ServerListMetadata;
  emitter = new EventEmitter();
  categorizers: string[] = [];
  error?: string;

  private active = false;
  private journalGeneration: number | null = null;
  // Local list generation and the query it shows.
  private listGeneration = 0;
  private shown?: {
    journalGeneration: number;
    filter: TransactionsFilter;
    listGeneration: number;
  };
  // Key of the last count request; a response for another key is stale.
  private requestedKey?: string;
  private retryTimer?: number;
  // Row to scroll to once the list of this journal generation is shown.
  private pendingScroll?: { index: number; journalGeneration: number | null };

  cache = new ServerListCache<TransactionItem>(
    (generation, startIndex, endIndex) => {
      // The fetch is bound to the query of its own list generation.
      const { shown } = this;
      if (shown === undefined || shown.listGeneration !== generation) {
        return Promise.reject(new Error("stale list generation"));
      }
      return fetchPage(
        shown.filter,
        shown.journalGeneration,
        startIndex,
        endIndex - startIndex
      ).then(page => page.items);
    },
    "transactions"
  );

  private serverListener = (state: Partial<ServerState>) => {
    const generation = state.journal_generation ?? null;
    if (generation !== this.journalGeneration) {
      this.journalGeneration = generation;
      this.refresh();
    }
  };

  constructor(
    private serverConnection: ServerConnection,
    public filter: TransactionsFilter
  ) {
    serverConnection.addListener(this.serverListener, true);
  }

  /** Query of the rows shown, undefined before the first list. */
  get shownQuery():
    | { journalGeneration: number; filter: TransactionsFilter }
    | undefined {
    const { shown } = this;
    return shown && { journalGeneration: shown.journalGeneration, filter: shown.filter };
  }

  get count(): number | undefined {
    return this.metadata && this.metadata[1];
  }

  dispose() {
    this.serverConnection.removeListener(this.serverListener);
  }

  setActive(active: boolean) {
    this.active = active;
    if (
      active &&
      this.shown !== undefined &&
      this.shown.journalGeneration !== this.journalGeneration
    ) {
      // The journal changed while the tab was hidden: drop the stale rows.
      this.clearList();
    }
    this.refresh();
  }

  /** Scrolls to row `index` of the list of the current journal generation. */
  scrollToRow(index: number) {
    this.pendingScroll = { index, journalGeneration: this.journalGeneration };
    this.applyPendingScroll();
  }

  private applyPendingScroll() {
    const { pendingScroll, shown } = this;
    if (
      pendingScroll !== undefined &&
      shown !== undefined &&
      shown.journalGeneration === pendingScroll.journalGeneration
    ) {
      this.pendingScroll = undefined;
      this.scrollState.scrollToIndex(pendingScroll.index);
    }
  }

  private clearList() {
    this.metadata = [++this.listGeneration, 0];
    this.emitter.emit("change", this.metadata);
    this.emitter.emit("update");
  }

  setFilter(filter: TransactionsFilter) {
    this.filter = filter;
    try {
      window.localStorage.setItem(
        FILTER_STORAGE_KEY,
        JSON.stringify(storedFilter(filter, new Date()))
      );
    } catch (_) {
      /* storage unavailable */
    }
    this.refresh();
    this.emitter.emit("update");
  }

  private refresh() {
    const { active, journalGeneration, filter } = this;
    if (!active || journalGeneration === null) {
      return;
    }
    if (filter.start && filter.end && filter.start > filter.end) {
      this.requestedKey = undefined;
      this.error = "Start date is after end date.";
      this.clearList();
      return;
    }
    const key = JSON.stringify([journalGeneration, filter]);
    if (key === this.requestedKey) {
      return;
    }
    this.requestedKey = key;
    window.clearTimeout(this.retryTimer);
    fetchPage(filter, journalGeneration, 0, 0).then(
      page => {
        if (key !== this.requestedKey) {
          return;
        }
        const filterChanged =
          this.shown !== undefined && this.shown.filter !== filter;
        this.listGeneration++;
        this.shown = {
          journalGeneration,
          filter,
          listGeneration: this.listGeneration
        };
        this.categorizers = page.categorizers;
        this.error = undefined;
        this.metadata = [this.listGeneration, page.count];
        this.emitter.emit("change", this.metadata);
        this.emitter.emit("update");
        if (filterChanged) {
          this.scrollState.scrollToIndex(0);
        }
        this.applyPendingScroll();
      },
      error => {
        if (key !== this.requestedKey) {
          return;
        }
        console.log(`Error retrieving transactions: ${error}`);
        if (error instanceof FetchError && error.status === 400) {
          // The request itself is invalid; retrying cannot help.
          this.error = error.serverMessage || String(error);
          this.clearList();
          return;
        }
        // A stale generation (404) during a reload, a network or server error.
        this.requestedKey = undefined;
        this.retryTimer = window.setTimeout(() => this.refresh(), retryDelay);
      }
    );
  }
}

class TransactionsVirtualListComponent extends ServerVirtualListComponent<
  TransactionItem
> {}

const TransactionsListElement = styled(TransactionsVirtualListComponent)`
  overflow-y: scroll;
  flex: 1;
  flex-basis: 0px;
`;

const TransactionElement = styled.div<{ selected: boolean }>`
  cursor: pointer;
  padding: 12px 8px;
  border-bottom: 1px solid var(--color-main-accent);
  min-width: 100%;
  box-sizing: border-box;

  :hover {
    background-color: var(--color-hover-bg);
    color: var(--color-hover-text);
  }
  ${props =>
    props.selected &&
    `
    &, &:hover {
      background-color: var(--color-select-bg);
      color: var(--color-select-text);
    }
    `}
`;

const TransactionsPanel = styled.div`
  display: flex;
  flex-direction: column;
  flex: 1;
`;

const allCategorizers = "\u0000all";

interface TransactionsComponentProps {
  listState: TransactionsListState;
  // The transaction open in Candidates for editing; its row is highlighted.
  review?: ReviewState | null;
  // `journalGeneration`, `filter`: the query of the list the row comes from.
  onOpen: (
    item: TransactionItem,
    journalGeneration: number,
    filter: TransactionsFilter
  ) => void;
}

interface TransactionsComponentState {
  // The search box; applied to the filter after a pause in typing.
  search: string;
}

export class TransactionsComponent extends React.PureComponent<
  TransactionsComponentProps,
  TransactionsComponentState
> {
  state: TransactionsComponentState = {
    search: this.props.listState.filter.search
  };
  private subscription?: EventSubscription;
  private searchTimer?: number;

  componentDidMount() {
    const { listState } = this.props;
    this.subscription = listState.emitter.addListener("update", () =>
      this.forceUpdate()
    );
    listState.setActive(true);
  }

  componentWillUnmount() {
    this.subscription!.remove();
    window.clearTimeout(this.searchTimer);
    this.props.listState.setActive(false);
    this.applySearch();
  }

  private setFilter(changes: Partial<TransactionsFilter>) {
    const { listState } = this.props;
    listState.setFilter({ ...listState.filter, ...changes });
  }

  private applySearch = () => {
    if (this.state.search !== this.props.listState.filter.search) {
      this.setFilter({ search: this.state.search });
    }
  };

  private handleSearch = (event: React.ChangeEvent<HTMLInputElement>) => {
    this.setState({ search: event.target.value });
    window.clearTimeout(this.searchTimer);
    this.searchTimer = window.setTimeout(this.applySearch, searchDelay);
  };

  private handlePreset(name: DatePreset) {
    this.setFilter(datePreset(name, new Date()));
  }

  private handleCategorizer = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const { value } = event.target;
    this.setFilter({ categorizer: value === allCategorizers ? null : value });
  };

  private renderItem(
    item: TransactionItem,
    index: number,
    ref: React.RefObject<any>
  ) {
    const { review } = this.props;
    return (
      <TransactionElement
        key={index}
        ref={ref}
        selected={
          review != null &&
          review.filename === item.filename &&
          review.lineno === item.lineno
        }
        title={`${item.filename}${
          item.lineno != null ? `:${item.lineno}` : ""
        }: click to edit`}
        onClick={() => {
          const query = this.props.listState.shownQuery;
          if (query !== undefined) {
            this.props.onOpen(item, query.journalGeneration, query.filter);
          }
        }}
      >
        <PendingEntryFormattedElement>{item.formatted}</PendingEntryFormattedElement>
      </TransactionElement>
    );
  }

  render() {
    const { listState } = this.props;
    const { filter, categorizers, count, error } = listState;
    const preset = activePreset(filter, new Date());
    const options = [...categorizers];
    if (filter.categorizer && options.indexOf(filter.categorizer) < 0) {
      options.push(filter.categorizer);
    }
    return (
      <TransactionsPanel>
        <div className="action-button-wrapper transactions-toolbar">
          <div className="action-button__group transactions-dates">
            <input
              type="date"
              aria-label="From"
              title="From"
              value={filter.start}
              onChange={event => this.setFilter({ start: event.target.value })}
            />
            <input
              type="date"
              aria-label="To"
              title="To"
              value={filter.end}
              onChange={event => this.setFilter({ end: event.target.value })}
            />
          </div>
          <div className="action-button__group">
            {datePresets.map(([name, label]) => (
              <button
                key={name}
                className={
                  "action-button" + (name === preset ? " action-button--active" : "")
                }
                onClick={() => this.handlePreset(name)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="action-button__group">
            <select
              title="Categorizer metadata of any posting"
              value={filter.categorizer ?? allCategorizers}
              onChange={this.handleCategorizer}
            >
              <option value={allCategorizers}>categorizer: All</option>
              <option value="">categorizer: (none)</option>
              {options.map(value => (
                <option key={value} value={value}>
                  categorizer: {value}
                </option>
              ))}
            </select>
          </div>
          <div className="action-button__group">
            <input
              type="search"
              placeholder="Search"
              title="Payee, narration, metadata and accounts; ignores case and punctuation"
              value={this.state.search}
              onChange={this.handleSearch}
            />
          </div>
          <div className="action-button__group transactions-count">
            {error ??
              (count === undefined
                ? "Loading…"
                : `${count} transaction${count === 1 ? "" : "s"}`)}
          </div>
        </div>
        <TransactionsListElement
          listState={listState}
          // A new function on every render, so the highlight follows `review`.
          renderItem={this.renderItem.bind(this)}
        />
      </TransactionsPanel>
    );
  }
}
