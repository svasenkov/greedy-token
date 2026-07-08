/** Pyramid layers — same keys as TestOps (@Layer / label layer). */

export const REPORT_LANGUAGE = "en";

export const PYRAMID_LAYERS = [
  "unit",
  "component",
  "integration",
  "api",
  "e2e",
  "manual",
];

export const STABILITY_THRESHOLD = 90;

export const STABILITY_SKIP_STATUSES = ["skipped", "unknown"];

export const HISTORY_DEFAULTS = {
  historyPath: "./history.jsonl",
  appendHistory: true,
  historyLimit: 20,
  knownIssuesPath: "./known.json",
};

export const TITLES = {
  currentStatus: "Current status",
  testingPyramid: "Testing pyramid",
  testResultSeverities: "Results by severity",
  statusDynamics: "Status dynamics",
  statusTransitions: "Status transitions",
  testBaseGrowthDynamics: "Test base growth",
  durations: "Duration histogram",
  durationsByLayer: "Durations by layer",
  durationDynamics: "Duration dynamics",
  successRateDistribution: "Success rate distribution",
  stabilityByModule: "Stability by module",
};
