import * as React from "react";
import * as ReactDOM from "react-dom";
import {
  Candidate,
  BeancountTransaction,
  secretKey,
} from "./server_connection";
import {
  JEV_SETTINGS_EVENT,
  isJevAnswerValid,
  jevSkipMatch,
  loadJevAccountFilters,
  loadJevInstructions,
  loadJevSkipPatterns,
} from "./jev_settings";

interface HistoryRow {
  account: string;
  count: number;
  narrations: { narration: string; count: number }[];
}
interface Classification {
  category: string;
  confidence: number;
  probabilities?: { [account: string]: number };
}
interface Props {
  candidate: Candidate;
  candidateIndex: number;
  generation: number;
  configured: boolean;
  toolbar: HTMLElement | null;
}
interface State {
  query: string;
  group?: number;
  rows: HistoryRow[];
  count: number;
  historyLoading: boolean;
  historyError?: string;
  aiOpen: boolean;
  loading: boolean;
  result?: Classification;
  aiError?: string;
  previewOpen: boolean;
  preview?: string;
  previewError?: string;
  applyError?: string;
}

export function percent(value: number) {
  return value > 0 && value < 0.0001
    ? "<0.01%"
    : `${Math.round(value * 10000) / 100}%`;
}

export class HistoryHintsComponent extends React.Component<Props, State> {
  state: State = {
    query: this.props.candidate.hints ? this.props.candidate.hints.payee : "",
    group: this.defaultGroup(),
    rows: [],
    count: 0,
    historyLoading: false,
    aiOpen: false,
    loading: false,
    previewOpen: false,
  };
  private aiPanel = React.createRef<HTMLElement>();
  private mounted = false;
  private epoch = 0;
  private historySequence = 0;
  private historyTimer?: number;
  private controllers = new Set<AbortController>();
  private inFlight = false;

  private defaultGroup() {
    const hints = this.props.candidate.hints;
    return hints && hints.groups.length === 1
      ? hints.groups[0].number
      : undefined;
  }

  componentDidMount() {
    this.mounted = true;
    window.addEventListener(JEV_SETTINGS_EVENT, this.handleSettingsChange);
    this.loadHistory();
  }
  componentWillUnmount() {
    this.mounted = false;
    window.removeEventListener(JEV_SETTINGS_EVENT, this.handleSettingsChange);
    this.invalidate();
  }
  private handleSettingsChange = () => this.forceUpdate();
  componentDidUpdate(previous: Props) {
    const before = previous.candidate.hints;
    const after = this.props.candidate.hints;
    if ((before && before.token) !== (after && after.token)) {
      this.invalidate();
      const payeeChanged = (before && before.payee) !== (after && after.payee);
      this.setState(
        {
          query: payeeChanged ? (after ? after.payee : "") : this.state.query,
          group: this.defaultGroup(),
          aiOpen: false,
          loading: false,
          result: undefined,
          aiError: undefined,
          previewOpen: false,
          preview: undefined,
          previewError: undefined,
          applyError: undefined,
        },
        this.loadHistory
      );
    }
  }
  private invalidate() {
    ++this.epoch;
    ++this.historySequence;
    this.inFlight = false;
    window.clearTimeout(this.historyTimer);
    this.controllers.forEach((controller) => controller.abort());
    this.controllers.clear();
  }
  private get disabledReason() {
    const hints = this.props.candidate.hints;
    if (!hints || !hints.groups.length)
      return "This candidate has no editable posting groups.";
    if (this.state.group === undefined) return "Select a posting group first.";
    return "";
  }
  private get aiDisabledReason() {
    const hints = this.props.candidate.hints;
    const skip =
      hints && jevSkipMatch(loadJevSkipPatterns(), hints.transaction);
    if (skip !== undefined)
      return `Skipped for AI by the settings pattern ${skip}`;
    if (this.disabledReason) return this.disabledReason;
    if (!this.props.configured)
      return "Set OPENROUTER_API_KEY on the importer server.";
    return "";
  }
  private async request(operation: string, extra: object = {}) {
    const controller = new AbortController();
    this.controllers.add(controller);
    const hints = this.props.candidate.hints;
    try {
      const response = await fetch(
        `${window.location.pathname}${secretKey}/${operation}`,
        {
          method: "POST",
          signal: controller.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            generation: this.props.generation,
            candidate_index: this.props.candidateIndex,
            token: hints && hints.token,
            group_number: this.state.group,
            account_filters: loadJevAccountFilters(),
            skip_patterns: loadJevSkipPatterns(),
            instructions: loadJevInstructions(),
            ...extra,
          }),
        }
      );
      if (!response.ok) {
        let message = `Request failed (${response.status}). Reload hints and try again.`;
        try {
          message = (await response.json()).error || message;
        } catch (_) {
          /* HTTP error page */
        }
        throw new Error(message);
      }
      return await response.json();
    } finally {
      this.controllers.delete(controller);
    }
  }
  private isCurrent(epoch: number) {
    return this.mounted && epoch === this.epoch;
  }
  private loadHistory = async () => {
    if (!this.props.candidate.hints) return;
    const epoch = this.epoch;
    const sequence = ++this.historySequence;
    this.setState({ historyLoading: true, historyError: undefined });
    try {
      const result = await this.request("hints/history", {
        query: this.state.query,
      });
      if (this.isCurrent(epoch) && sequence === this.historySequence)
        this.setState({
          rows: result.accounts,
          count: result.transaction_count,
          historyLoading: false,
        });
    } catch (error) {
      if (this.isCurrent(epoch) && sequence === this.historySequence)
        this.setState({ historyError: String(error), historyLoading: false });
    }
  };
  private search = (event: React.ChangeEvent<HTMLInputElement>) => {
    ++this.historySequence;
    window.clearTimeout(this.historyTimer);
    this.setState({ query: event.target.value, historyLoading: true });
    this.historyTimer = window.setTimeout(this.loadHistory, 180);
  };
  private selectGroup = (event: React.ChangeEvent<HTMLSelectElement>) => {
    this.invalidate();
    this.setState(
      {
        group:
          event.target.value === "" ? undefined : Number(event.target.value),
        aiOpen: false,
        loading: false,
        result: undefined,
        aiError: undefined,
        previewOpen: false,
        preview: undefined,
        previewError: undefined,
        applyError: undefined,
      },
      this.loadHistory
    );
  };
  private scrollAiIntoView = () => {
    if (this.aiPanel.current)
      this.aiPanel.current.scrollIntoView({ block: "nearest" });
  };
  classify = async () => {
    if (this.aiDisabledReason || this.inFlight) return;
    this.inFlight = true;
    const epoch = this.epoch;
    this.setState(
      {
        aiOpen: true,
        loading: true,
        result: undefined,
        aiError: undefined,
      },
      this.scrollAiIntoView
    );
    try {
      const result = await this.request("hints/classify");
      if (this.isCurrent(epoch))
        this.setState({ result, loading: false }, this.scrollAiIntoView);
    } catch (error) {
      if (this.isCurrent(epoch))
        this.setState(
          { aiError: String(error), loading: false },
          this.scrollAiIntoView
        );
    } finally {
      if (this.isCurrent(epoch)) this.inFlight = false;
    }
  };
  private togglePreview = async () => {
    if (this.disabledReason) return;
    if (this.state.previewOpen) {
      this.setState({ previewOpen: false });
      return;
    }
    const epoch = this.epoch;
    this.setState({
      previewOpen: true,
      preview: undefined,
      previewError: undefined,
    });
    try {
      const result = await this.request("hints/request");
      if (this.isCurrent(epoch))
        this.setState({ preview: JSON.stringify(result.request, null, 2) });
    } catch (error) {
      if (this.isCurrent(epoch)) this.setState({ previewError: String(error) });
    }
  };
  private apply = async (account: string) => {
    if (this.disabledReason) return;
    await this.changeCandidate(account);
  };
  private applyNarration = async (narration: string) => {
    await this.changeCandidate(undefined, narration);
  };
  private changeCandidate = async (account?: string, narration?: string) => {
    if (!this.props.candidate.hints) return;
    const epoch = this.epoch;
    const candidate = this.props.candidate;
    const transaction = candidate.new_entries.find(
      (entry) => "postings" in entry
    ) as BeancountTransaction;
    this.setState({ applyError: undefined });
    try {
      await this.request("change_candidate", {
        hint_token: candidate.hints!.token,
        changes: {
          accounts: candidate.substituted_accounts.map((s) =>
            account !== undefined && s[2] === this.state.group ? account : s[1]
          ),
          payee: transaction.payee,
          narration: narration === undefined ? transaction.narration : narration,
          tags: transaction.tags,
          links: transaction.links,
        },
      });
    } catch (error) {
      if (this.isCurrent(epoch)) this.setState({ applyError: String(error) });
    }
  };
  render() {
    const hints = this.props.candidate.hints;
    const reason = this.disabledReason;
    const { result } = this.state;
    const aiReason = this.aiDisabledReason;
    const valid = !!result && isJevAnswerValid(result.confidence);
    const answerClass = (account: string) =>
      valid && account === result!.category
        ? "hints-category hints-valid"
        : "hints-category";
    const rows: [string, number][] =
      result && result.probabilities
        ? Object.keys(result.probabilities)
            .map(
              (account) =>
                [account, result.probabilities![account]] as [string, number]
            )
            .filter(([, p]) => p > 0)
            .sort((a, b) => b[1] - a[1])
        : [];
    return (
      <>
        {this.props.toolbar &&
          ReactDOM.createPortal(
            <>
              <button
                className="action-button"
                onClick={this.classify}
                disabled={!!aiReason || this.state.loading}
                title={
                  aiReason ||
                  "Classify the selected posting group with JEV; sends transaction text, categories and payee counts to OpenRouter. Keyboard shortcut: c"
                }
              >
                Classify with AI
              </button>
              <button
                className="action-button"
                onClick={this.togglePreview}
                disabled={!!reason}
                title={
                  reason ||
                  "Preview the JEV request locally. No external request is sent."
                }
              >
                {this.state.previewOpen ? "Hide request" : "Show request"}
              </button>
            </>,
            this.props.toolbar
          )}
        {hints && (
          <div className="history-hints">
            <section aria-label="Payee history">
              <div className="hints-heading hints-history-heading">
                <span className="hints-heading-title">Payee history</span>
                <input
                  className="hints-search"
                  aria-label="Search history"
                  placeholder="Search history"
                  value={this.state.query}
                  onChange={this.search}
                />
              </div>
              <div className="hints-controls">
                {hints.groups.length > 1 && (
                  <label>
                    Posting group{" "}
                    <select
                      aria-label="Posting group"
                      value={
                        this.state.group === undefined ? "" : this.state.group
                      }
                      onChange={this.selectGroup}
                    >
                      <option value="">Select a group…</option>
                      {hints.groups.map((group) => (
                        <option key={group.number} value={group.number}>
                          {group.number + 1}: {group.postings.join("; ")}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {hints.groups.length === 1 && (
                  <div>
                    Group {hints.groups[0].number + 1}:{" "}
                    {hints.groups[0].postings.join("; ")}
                  </div>
                )}
                {(reason || aiReason) && (
                  <div className="hints-note">{reason || aiReason}</div>
                )}
                {this.state.applyError && (
                  <div role="alert">{this.state.applyError}</div>
                )}
              </div>
              {this.state.historyLoading ? (
                <div className="hints-note" role="status">
                  Loading history…
                </div>
              ) : this.state.historyError ? (
                <div className="hints-note" role="alert">
                  {this.state.historyError}
                </div>
              ) : (
                <>
                  <div className="hints-note">
                    {this.state.count} matching transactions
                  </div>
                  {this.state.rows.length === 0 && (
                    <div className="hints-note">
                      No expense or income categories found.
                    </div>
                  )}
                  <div className="hints-rows">
                    {this.state.rows.map((row) => (
                      <div key={row.account} className="hints-history-row">
                        <button
                          className="hints-category"
                          disabled={!!reason}
                          title={
                            reason ||
                            "Apply to the selected group; Confirm saves the transaction"
                          }
                          onClick={() => this.apply(row.account)}
                        >
                          <span>{row.account}</span>
                          <span>{row.count}</span>
                        </button>
                        {row.narrations.map((n) => (
                          <button
                            className="hints-narration"
                            key={n.narration}
                            title="Use this narration for the current candidate; Confirm saves the transaction"
                            onClick={() => this.applyNarration(n.narration)}
                          >
                            <span>{n.narration}</span>
                            <span>{n.count}</span>
                          </button>
                        ))}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </section>
            {this.state.previewOpen && (
              <section aria-label="JEV request">
                <div className="hints-heading">JEV request — local preview</div>
                <pre className="hints-json">
                  {this.state.previewError ||
                    this.state.preview ||
                    "Preparing request…"}
                </pre>
              </section>
            )}
            {this.state.aiOpen && (
              <section aria-label="AI Category" ref={this.aiPanel}>
                <div className="hints-heading">
                  AI Category{" "}
                  <span title="JEV selects an expense or income category. Probabilities rank the options; confidence describes the distribution, not guaranteed correctness. Transaction text and metadata, categories and payee counts are sent to OpenRouter. No automatic retries or caching.">
                    ⓘ
                  </span>
                </div>
                {this.state.loading && (
                  <div className="hints-note" role="status">
                    Classifying with JEV…
                  </div>
                )}
                {this.state.aiError && (
                  <div className="hints-note" role="alert">
                    {this.state.aiError}
                  </div>
                )}
                {result && (
                  <>
                    <div className="hints-rows">
                      {result.probabilities ? (
                        rows.map(([account, probability]) => (
                          <button
                            className={answerClass(account)}
                            key={account}
                            onClick={() => this.apply(account)}
                          >
                            <span>{account}</span>
                            <span>{percent(probability)}</span>
                          </button>
                        ))
                      ) : (
                        <>
                          <button
                            className={answerClass(result.category)}
                            onClick={() => this.apply(result.category)}
                          >
                            {result.category}
                          </button>
                          <div className="hints-note">
                            Category probabilities are unavailable.
                          </div>
                        </>
                      )}
                    </div>
                    <div className="hints-note">
                      Overall confidence:{" "}
                      <strong>{percent(result.confidence)}</strong>
                      {valid ? " (valid)" : " (below the valid threshold)"}
                    </div>
                  </>
                )}
              </section>
            )}
          </div>
        )}
      </>
    );
  }
}
