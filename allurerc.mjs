/** Allure 3 config for greedy-token pytest (aligned with stacks/java-spring/tests ethalon). */

const ALLURE_VERSION = "3.13.0";

export default {
  name: "greedy-token Tests",
  historyPath: "./history.jsonl",
  appendHistory: true,
  historyLimit: 20,
  variables: {
    Framework: "pytest",
    Report: `Allure ${ALLURE_VERSION}`,
  },
  qualityGate: {
    rules: [
      { maxFailures: 0 },
      { minTestsCount: 80 },
    ],
  },
  plugins: {
    awesome: {
      options: {
        reportLanguage: "en",
        groupBy: ["parentSuite", "suite", "subSuite"],
      },
    },
    dashboard: {
      options: {
        reportName: "greedy-token Tests Dashboard",
        reportLanguage: "en",
      },
    },
    csv: {
      options: {
        fileName: "greedy-token.csv",
      },
    },
  },
};
