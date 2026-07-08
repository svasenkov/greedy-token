import {
  PYRAMID_LAYERS,
  STABILITY_SKIP_STATUSES,
  STABILITY_THRESHOLD,
  TITLES,
} from "./constants.mjs";

/** Awesome charts — index 0 = currentStatus, index 1 = testingPyramid. */
export function buildAwesomeCharts() {
  return [
    {
      type: "currentStatus",
      title: TITLES.currentStatus,
    },
    {
      type: "testingPyramid",
      title: TITLES.testingPyramid,
      layers: [...PYRAMID_LAYERS],
    },
    {
      type: "testResultSeverities",
      title: TITLES.testResultSeverities,
    },
    {
      type: "statusDynamics",
      title: TITLES.statusDynamics,
      limit: 20,
    },
    {
      type: "statusTransitions",
      title: TITLES.statusTransitions,
      limit: 20,
    },
    {
      type: "testBaseGrowthDynamics",
      title: TITLES.testBaseGrowthDynamics,
      limit: 20,
    },
    {
      type: "successRateDistribution",
      title: TITLES.successRateDistribution,
    },
    {
      type: "stabilityDistribution",
      title: TITLES.stabilityByModule,
      threshold: STABILITY_THRESHOLD,
      skipStatuses: [...STABILITY_SKIP_STATUSES],
      groupBy: "parentSuite",
    },
    {
      type: "durations",
      title: TITLES.durations,
      groupBy: "none",
    },
    {
      type: "durations",
      title: TITLES.durationsByLayer,
      groupBy: "layer",
    },
    {
      type: "durationDynamics",
      title: TITLES.durationDynamics,
      limit: 20,
    },
  ];
}
