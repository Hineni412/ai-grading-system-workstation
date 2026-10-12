import assert from "node:assert/strict";
import test from "node:test";
import { imagePointFromClient, pushHistory, reconcileRegionsByUuid } from "./editor.js";

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

test("imagePointFromClient converts client coordinates into SVG viewBox coordinates", () => {
  const point = imagePointFromClient(
    { x: 350, y: 225 },
    { x: 100, y: 50, width: 500, height: 350 },
    { x: 20, y: 40, width: 1000, height: 700 },
  );

  assert.deepEqual(point, { x: 520, y: 390 });
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
