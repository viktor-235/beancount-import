// Pure helpers for JEV answers and the batch buttons (Accept with AI,
// Accept rules + AI). The settings themselves live in the server's settings
// file and reach the browser with the server state.

import { JevRun } from "./server_connection";

export type JevBatchMode = "ai" | "rules_ai";

export function percent(value: number) {
  return value > 0 && value < 0.0001
    ? "<0.01%"
    : `${Math.round(value * 10000) / 100}%`;
}

// Whether a JEV answer is confident enough to be trusted; the batch run
// confirms exactly these. Both values are fractions in [0, 1].
export function isJevAnswerValid(confidence: number, validConfidence: number) {
  return confidence >= validConfidence;
}

export interface JevBatchStatus {
  rulesPending: number;
  aiPending: number;
  jevEnabled: boolean;
  jevSettingsError?: string | null;
  run?: JevRun | null;
}

// Transactions the button handles: rules and AI sets are disjoint.
export function batchCount(mode: JevBatchMode, status: JevBatchStatus) {
  return mode === "ai"
    ? status.aiPending
    : status.rulesPending + status.aiPending;
}

export function isBatchRunning(run?: JevRun | null): boolean {
  return !!run && run.running;
}

export function batchLabel(mode: JevBatchMode, status: JevBatchStatus) {
  const { run } = status;
  if (isBatchRunning(run) && run!.mode === mode)
    return `Stop (${run!.i}/${run!.n})`;
  const name = mode === "ai" ? "Accept with AI" : "Accept rules + AI";
  return `${name} (${batchCount(mode, status)})`;
}

// Why the button is disabled, or "" if it is enabled. The running button
// stays enabled: it stops the run.
export function batchDisabledReason(
  mode: JevBatchMode,
  status: JevBatchStatus
): string {
  const { run } = status;
  if (isBatchRunning(run))
    return run!.mode === mode ? "" : "An AI batch run is in progress.";
  if (status.jevSettingsError) return `JEV disabled: ${status.jevSettingsError}`;
  if (!status.jevEnabled)
    return "Set OPENROUTER_API_KEY on the importer server.";
  if (batchCount(mode, status) === 0) return "Nothing to accept.";
  return "";
}

export function batchConfirmText(aiPending: number, validConfidence: number) {
  const transactions =
    aiPending === 1 ? "1 transaction" : `${aiPending} transactions`;
  return (
    `Send ${transactions} to OpenRouter (JEV) for classification?\n\n` +
    "Each transaction is a separate paid request. Answers with a confidence " +
    `of at least ${percent(validConfidence)} are confirmed automatically; ` +
    "the others stay pending."
  );
}
