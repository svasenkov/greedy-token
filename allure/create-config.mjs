import { buildAwesomeCharts } from "./awesome-charts.mjs";
import { HISTORY_DEFAULTS, REPORT_LANGUAGE } from "./constants.mjs";
import { buildDashboardLayout } from "./dashboard-layout.mjs";
import { qualityGateRules } from "./quality-gate.mjs";

/**
 * @param {{ slug: string, variables?: Record<string, string> }} profile
 */
export function createAllureConfig({ slug, variables } = {}) {
  if (!slug || typeof slug !== "string") {
    throw new Error("createAllureConfig: profile.slug is required");
  }

  return {
    name: `${slug} Tests`,
    ...HISTORY_DEFAULTS,
    variables: variables ?? {
      Framework: "pytest",
      Report: "Allure 3",
    },
    qualityGate: {
      rules: qualityGateRules.map((rule) => ({ ...rule })),
    },
    plugins: {
      awesome: {
        options: {
          reportLanguage: REPORT_LANGUAGE,
          groupBy: ["parentSuite", "suite", "subSuite"],
          charts: buildAwesomeCharts(),
        },
      },
      dashboard: {
        options: {
          reportName: `${slug} Tests`,
          reportLanguage: REPORT_LANGUAGE,
          layout: buildDashboardLayout(),
        },
      },
      csv: {
        options: {
          fileName: `${slug}.csv`,
        },
      },
    },
  };
}
