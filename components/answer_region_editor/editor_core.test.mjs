import assert from "node:assert/strict";
import test from "node:test";

import {
  clampRegion,
  filterRegionsByPage,
  imagePointFromClient,
  pushHistory,
  reconcileRegionsByUuid,
  removeRegionByUuid,
} from "./editor.js";

test("reconcile uses region_uuid and incoming order without mutating inputs", () => {
  const previous = [
    { region_uuid: "a", page: "front", x: 1, y: 2, w: 30, h: 40, mapping_status: "auto" },
    { region_uuid: "b", page: "back", x: 50, y: 60, w: 70, h: 80, mapping_status: "manual" },
  ];
  const incoming = [
    { region_uuid: "b", page: "back", x: 55, y: 65, w: 70, h: 80 },
    { region_uuid: "a", page: "front", x: 1, y: 2, w: 30, h: 40 },
  ];
  const previousBefore = structuredClone(previous);
  const incomingBefore = structuredClone(incoming);

  const result = reconcileRegionsByUuid(previous, incoming);

  assert.deepEqual(result.map((region) => region.region_uuid), ["b", "a"]);
  assert.equal(result[0].x, 55);
  assert.equal(result[0].mapping_status, "manual");
  assert.equal(result[1].x, 1);
  assert.deepEqual(previous, previousBefore);
  assert.deepEqual(incoming, incomingBefore);
  assert.notEqual(result[0], previous[1]);
  assert.notEqual(result[0], incoming[0]);
});

test("reconcile deletion does not retain or rewrite another UUID", () => {
  const previous = [
    { region_uuid: "a", x: 1, mapped_question_id: "Q1" },
    { region_uuid: "b", x: 20, mapped_question_id: "Q2" },
  ];

  const result = reconcileRegionsByUuid(previous, [
    { region_uuid: "b", x: 20, mapped_question_id: "Q2" },
  ]);

  assert.deepEqual(result, [{ region_uuid: "b", x: 20, mapped_question_id: "Q2" }]);
});

test("removeRegionByUuid deletes only the matching UUID and returns copies", () => {
  const regions = [
    { region_uuid: "a", x: 1 },
    { region_uuid: "b", x: 20 },
  ];

  const result = removeRegionByUuid(regions, "a");

  assert.deepEqual(result, [{ region_uuid: "b", x: 20 }]);
  assert.deepEqual(regions, [
    { region_uuid: "a", x: 1 },
    { region_uuid: "b", x: 20 },
  ]);
  assert.notEqual(result[0], regions[1]);
});

test("imagePointFromClient converts client coordinates into SVG viewBox coordinates", () => {
  const point = imagePointFromClient(
    { x: 350, y: 225 },
    { x: 100, y: 50, width: 500, height: 350 },
    { x: 20, y: 40, width: 1000, height: 700 },
  );

  assert.deepEqual(point, { x: 520, y: 390 });
});

test("clampRegion snaps slight edge overflow and preserves the input", () => {
  const region = { region_uuid: "a", x: -8, y: 10, w: 110, h: 95 };

  const result = clampRegion(region, { width: 100, height: 100 });

  assert.deepEqual(result, { region_uuid: "a", x: 0, y: 10, w: 100, h: 90 });
  assert.deepEqual(region, { region_uuid: "a", x: -8, y: 10, w: 110, h: 95 });
});

test("clampRegion preserves clearly out-of-bounds geometry for validation", () => {
  const region = { region_uuid: "a", x: -9, y: 10, w: 40, h: 30 };

  const result = clampRegion(region, { width: 100, height: 100 });

  assert.deepEqual(result, region);
  assert.notEqual(result, region);
});

test("filterRegionsByPage keeps front and back regions independent", () => {
  const regions = [
    { region_uuid: "front-a", page: "front", x: 1 },
    { region_uuid: "back-a", page: "back", x: 2 },
    { region_uuid: "front-b", page: "front", x: 3 },
  ];

  const front = filterRegionsByPage(regions, "front");
  const back = filterRegionsByPage(regions, "back");

  assert.deepEqual(front.map((region) => region.region_uuid), ["front-a", "front-b"]);
  assert.deepEqual(back.map((region) => region.region_uuid), ["back-a"]);
  assert.notEqual(front[0], regions[0]);
  front[0].x = 999;
  assert.equal(regions[0].x, 1);
});

test("pushHistory limits and isolates undo and redo snapshots", () => {
  const firstRegions = [{ region_uuid: "a", x: 1, metadata: { label: "Q1" } }];
  const firstHistory = pushHistory([], firstRegions, 2);
  const undoHistory = pushHistory(firstHistory, [{ region_uuid: "a", x: 2 }], 2);
  const redoHistory = pushHistory([], firstHistory[0], 2);
  const limited = pushHistory(undoHistory, [{ region_uuid: "a", x: 3 }], 2);

  firstRegions[0].x = 900;
  undoHistory[0][0].metadata.label = "changed";
  redoHistory[0][0].x = 700;

  assert.equal(firstHistory[0][0].x, 1);
  assert.equal(firstHistory[0][0].metadata.label, "Q1");
  assert.equal(undoHistory[1][0].x, 2);
  assert.equal(redoHistory[0][0].metadata.label, "Q1");
  assert.deepEqual(
    limited.map((snapshot) => snapshot[0].x),
    [2, 3],
  );
});
