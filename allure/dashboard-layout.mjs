import { PYRAMID_LAYERS, TITLES } from "./constants.mjs";

/** Dashboard layout — index 0 = currentStatus, index 1 = testingPyramid. */
export function buildDashboardLayout() {
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
      type: "statusDynamics",
      title: TITLES.statusDynamics,
      limit: 20,
    },
    {
      type: "durations",
      title: TITLES.durationsByLayer,
      groupBy: "layer",
    },
    {
      type: "testBaseGrowthDynamics",
      title: TITLES.testBaseGrowthDynamics,
      limit: 20,
    },
  ];
}
