import * as React from "react";
import styled from "styled-components";
import {
  DEFAULT_JEV_ACCOUNT_FILTERS,
  invalidPatterns,
  loadJevAccountFilters,
  loadJevInstructions,
  loadJevSkipPatterns,
  loadJevValidConfidencePercent,
  parseConfidencePercent,
  parsePatterns,
  saveJevAccountFilters,
  saveJevInstructions,
  saveJevSkipPatterns,
  saveJevValidConfidencePercent
} from "./jev_settings";

const SettingsComponentRoot = styled.div`
  position: relative;
`;

const SettingsComponentInnerContainer = styled.div`
  width: min(480px, calc(100vw - 16px));
  box-sizing: border-box;
  position: absolute;
  bottom: 24px;
  right: 0;
  max-height: calc(100vh - 48px);
  overflow-y: auto;
  padding: 8px;
  background-color: var(--color-main-bg);
  border: 1px solid var(--color-main-accent);
  border-radius: 5px;
`;

const SettingsButton = styled.button`
  font-family: var(--font-fam-sans);
  font-size: var(--font-size-sans-reg);
  border: 0;
  background-color: transparent;
  color: var(--color-main-text);
  cursor: pointer;
`;

const Label = styled.label`
  font-weight: bold;
  display: block;
  margin-bottom: 4px;
`;

const ThemeSelectorElm = styled.select`
  margin-bottom: 12px;
  cursor: pointer;
  width: 100%;
`;

const CandidatesPositionElm = styled.select`
  margin-bottom: 12px;
  cursor: pointer;
  width: 100%;
`;

const PatternsElm = styled.textarea`
  box-sizing: border-box;
  width: 100%;
  margin-bottom: 4px;
  font-family: var(--font-fam-mono);
  resize: vertical;
`;

const ConfidenceElm = styled.input`
  box-sizing: border-box;
  width: 6em;
  margin-bottom: 4px;
`;

const Hint = styled.div`
  font-size: smaller;
  margin-bottom: 12px;
`;

interface SettingsComponentProps {
  isOpen: boolean;
  onToggle: (open?: boolean) => void;
  onSettingsChange: (settings: SettingsComponentState) => void;
  jevDefaultInstructions: string;
}

export interface SettingsComponentState {
  theme: string;
  candidatesLeft: boolean;
  jevInclude: string;
  jevExclude: string;
  jevSkip: string;
  jevValidConfidence: string;
  // null: the server default prompt is used and shown.
  jevInstructions: string | null;
}

function applyTheme(theme: string) {
  // always remove all classes to handle "system default" case
  window.document.body.classList.remove("theme-dark");
  window.document.body.classList.remove("theme-light");

  if (theme === "dark") {
    window.document.body.classList.add("theme-dark");
  } else if (theme === "light") {
    window.document.body.classList.add("theme-light");
  }
}

export class SettingsComponent extends React.PureComponent<
  SettingsComponentProps,
  SettingsComponentState
> {
  state: SettingsComponentState = {
    theme: "auto",
    candidatesLeft: true,
    jevInclude: "",
    jevExclude: "",
    jevSkip: "",
    jevValidConfidence: "",
    jevInstructions: null
  };

  private handleThemeChange = ({
    target
  }: React.ChangeEvent<HTMLSelectElement>) => {
    const theme = target.value;
    this.setState({ theme });
    applyTheme(theme);
    window.localStorage.setItem("theme", theme);
  };

  private handleCandidatesPositionChange = ({
    target
  }: React.ChangeEvent<HTMLSelectElement>) => {
    const candidatesLeft = target.value === "true";
    this.setState({ candidatesLeft }, () => {
      this.props.onSettingsChange(this.state);
    });
    window.localStorage.setItem("candidatesLeft", candidatesLeft.toString());
  };

  private handleJevFiltersChange = (
    field: "jevInclude" | "jevExclude",
    text: string
  ) => {
    const texts = { ...this.state, [field]: text };
    this.setState({ [field]: text } as Pick<SettingsComponentState, typeof field>);
    saveJevAccountFilters({
      include: parsePatterns(texts.jevInclude),
      exclude: parsePatterns(texts.jevExclude)
    });
  };

  private handleJevFiltersReset = () => {
    saveJevAccountFilters(null);
    this.setState({
      jevInclude: DEFAULT_JEV_ACCOUNT_FILTERS.include.join("\n"),
      jevExclude: DEFAULT_JEV_ACCOUNT_FILTERS.exclude.join("\n")
    });
  };

  private handleJevSkipChange = ({
    target
  }: React.ChangeEvent<HTMLTextAreaElement>) => {
    this.setState({ jevSkip: target.value });
    saveJevSkipPatterns(parsePatterns(target.value));
  };

  private handleJevValidConfidenceChange = ({
    target
  }: React.ChangeEvent<HTMLInputElement>) => {
    this.setState({ jevValidConfidence: target.value });
    const percent = parseConfidencePercent(target.value);
    if (percent !== undefined) saveJevValidConfidencePercent(percent);
  };

  private handleJevInstructionsChange = ({
    target
  }: React.ChangeEvent<HTMLTextAreaElement>) => {
    this.setState({ jevInstructions: target.value });
    saveJevInstructions(target.value);
  };

  private handleJevInstructionsReset = () => {
    saveJevInstructions(null);
    this.setState({ jevInstructions: null });
  };

  componentDidMount() {
    const theme = window.localStorage.getItem("theme") || "auto";
    const candidatesLeft =
      (window.localStorage.getItem("candidatesLeft") || "true") === "true";
    const jevFilters = loadJevAccountFilters();
    applyTheme(theme);
    this.setState(
      {
        theme,
        candidatesLeft,
        jevInclude: jevFilters.include.join("\n"),
        jevExclude: jevFilters.exclude.join("\n"),
        jevSkip: loadJevSkipPatterns().join("\n"),
        jevValidConfidence: String(loadJevValidConfidencePercent()),
        jevInstructions: loadJevInstructions()
      },
      () => {
        this.props.onSettingsChange(this.state);
      }
    );
  }

  render() {
    const invalid = invalidPatterns([
      ...parsePatterns(this.state.jevInclude),
      ...parsePatterns(this.state.jevExclude),
      ...parsePatterns(this.state.jevSkip)
    ]);
    return (
      <SettingsComponentRoot>
        <SettingsButton onClick={() => this.props.onToggle()}>
          Settings
        </SettingsButton>
        {this.props.isOpen && (
          <SettingsComponentInnerContainer>
            <Label htmlFor="theme-selector">Theme</Label>
            <ThemeSelectorElm
              id="theme-selector"
              onChange={this.handleThemeChange}
              value={this.state.theme}
            >
              <option value="auto">System default</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </ThemeSelectorElm>

            <Label htmlFor="candidates-position">
              Candidates Pane Position
            </Label>
            <CandidatesPositionElm
              id="candidates-position"
              onChange={this.handleCandidatesPositionChange}
              value={this.state.candidatesLeft.toString()}
            >
              <option value="true">Left</option>
              <option value="false">Right</option>
            </CandidatesPositionElm>

            <Label htmlFor="jev-include">JEV Accounts: Include</Label>
            <PatternsElm
              id="jev-include"
              rows={3}
              spellCheck={false}
              value={this.state.jevInclude}
              onChange={(e) =>
                this.handleJevFiltersChange("jevInclude", e.target.value)
              }
            />
            <Label htmlFor="jev-exclude">JEV Accounts: Exclude</Label>
            <PatternsElm
              id="jev-exclude"
              rows={4}
              spellCheck={false}
              value={this.state.jevExclude}
              onChange={(e) =>
                this.handleJevFiltersChange("jevExclude", e.target.value)
              }
            />
            <Hint>
              One regular expression per line, searched in open account names.
              An account is sent to JEV if it matches any Include and no
              Exclude pattern.{" "}
              <a href="#" onClick={(e) => { e.preventDefault(); this.handleJevFiltersReset(); }}>
                Reset
              </a>
            </Hint>
            <Label htmlFor="jev-skip">JEV: Skip Transactions</Label>
            <PatternsElm
              id="jev-skip"
              rows={3}
              spellCheck={false}
              value={this.state.jevSkip}
              onChange={this.handleJevSkipChange}
            />
            <Hint>
              One regular expression per line, searched in the transaction text
              sent to JEV (payee, narration, metadata, postings; ^ and $ match
              at line starts and ends). Matching transactions are not
              classified with AI.
              {invalid.length > 0 && (
                <div role="alert">Invalid: {invalid.join(", ")}</div>
              )}
            </Hint>
            <Label htmlFor="jev-valid-confidence">
              JEV: Valid Confidence, %
            </Label>
            <ConfidenceElm
              id="jev-valid-confidence"
              type="number"
              min={0}
              max={100}
              step={1}
              value={this.state.jevValidConfidence}
              onChange={this.handleJevValidConfidenceChange}
            />
            <Hint>
              A JEV answer with at least this overall confidence counts as
              valid and is shown in bold.
              {parseConfidencePercent(this.state.jevValidConfidence) ===
                undefined && <div role="alert">Enter a number from 0 to 100.</div>}
            </Hint>
            <Label htmlFor="jev-instructions">JEV: Prompt</Label>
            <PatternsElm
              id="jev-instructions"
              rows={8}
              value={
                this.state.jevInstructions === null
                  ? this.props.jevDefaultInstructions
                  : this.state.jevInstructions
              }
              onChange={this.handleJevInstructionsChange}
            />
            <Hint>
              Instructions sent to JEV with each request.{" "}
              {this.state.jevInstructions === null
                ? "Default prompt."
                : "Custom prompt; an empty prompt uses the default."}{" "}
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  this.handleJevInstructionsReset();
                }}
              >
                Reset
              </a>
            </Hint>

            <p>
              For instructions on how to use beancount-import,{" "}
              <a
                target="_blank"
                href="https://github.com/jbms/beancount-import/blob/master/README.md#usage"
              >
                please see the Readme
              </a>
              .
            </p>
          </SettingsComponentInnerContainer>
        )}
      </SettingsComponentRoot>
    );
  }
}
