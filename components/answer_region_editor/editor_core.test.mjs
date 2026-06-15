import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

import answerRegionEditor, {
  clampRegion,
  imagePointFromClient,
  pushHistory,
  reconcileRegionsByUuid,
  removeRegionByUuid,
} from "./editor.js";
import * as editorModule from "./editor.js";

const editorSource = readFileSync(new URL("./editor.js", import.meta.url), "utf8");
const editorCss = readFileSync(new URL("./editor.css", import.meta.url), "utf8");

function loadLocalHelpers() {
  const instrumentedSource = editorSource
    .replace(
      /^export default function answerRegionEditor/m,
      "function answerRegionEditor",
    )
    .replace(/^export /gm, "")
    .concat(`
      globalThis.__testHelpers = {
        formatValidationIssue,
        newRegionUuid,
      };
    `);
  const context = vm.createContext({ crypto: {} });
  vm.runInContext(instrumentedSource, context);
  return context.__testHelpers;
}

test("public exports match the Task 5 contract exactly", () => {
  assert.equal(typeof answerRegionEditor, "function");
  assert.deepEqual(Object.keys(editorModule).sort(), [
    "clampRegion",
    "default",
    "imagePointFromClient",
    "pushHistory",
    "reconcileRegionsByUuid",
    "removeRegionByUuid",
  ]);
});

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

test("canvas rendering filters regions to the active front or back page locally", () => {
  assert.match(
    editorSource,
    /for \(const region of regions\.filter\(\(region\) => region\.page === activePage\)\)/,
  );
  assert.doesNotMatch(editorSource, /export function filterRegionsByPage/);
});

test("local validation formatter explains known issues and preserves backend messages", () => {
  const { formatValidationIssue } = loadLocalHelpers();
  const expected = new Map([
    ["unbound_question", "未绑定题目：请选择对应题目。"],
    ["region_out_of_bounds", "题框超出图像范围：请调整到页面内。"],
    ["region_too_small", "题框尺寸过小：请扩大题框。"],
    ["duplicate_uuid", "题框标识重复：请删除后重新创建该题框。"],
    ["unconfirmed_multi_region", "同题多框尚未确认：请确认这些题框属于同一道题。"],
    ["template_mismatch", "试卷模板不匹配：请重新检查当前标定。"],
  ]);

  for (const [code, message] of expected) {
    assert.equal(formatValidationIssue({ code }), message);
  }
  assert.equal(
    formatValidationIssue({
      code: "region_too_small",
      message: "Width must be at least 8.",
    }),
    "题框尺寸过小：请扩大题框。（Width must be at least 8.）",
  );
});

test("drawer and status render formatted validation explanations", () => {
  assert.match(editorSource, /validationIssues\.map\(formatValidationIssue\)/);
  assert.match(editorSource, /issuesForRegion\(region\)\.map\(formatValidationIssue\)/);
  assert.match(editorSource, /issueList\.className = "region-issues"/);
});

test("UUID fallback generates RFC 4122-style version 4 UUIDs", () => {
  const { newRegionUuid } = loadLocalHelpers();
  const values = Array.from({ length: 12 }, () => newRegionUuid());
  const uuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  assert.equal(new Set(values).size, values.length);
  for (const value of values) {
    assert.match(value, uuidV4);
  }
});

test("space panning state resets on blur and visibility loss through cleaned-up listeners", () => {
  assert.match(editorSource, /function resetSpacePressed\(\) \{\s*spacePressed = false;\s*\}/);
  assert.match(editorSource, /listen\(window, "blur", resetSpacePressed\);/);
  assert.match(editorSource, /listen\(document, "visibilitychange", handleVisibilityChange\);/);
  assert.match(
    editorSource,
    /for \(const removeListener of listeners\.splice\(0\)\) \{\s*removeListener\(\);\s*\}/,
  );
});

test("keyboard-focusable editor root has a visible focus-visible style", () => {
  assert.match(editorCss, /\.region-editor:focus-visible\s*\{[^}]*outline:/s);
  assert.doesNotMatch(editorCss, /\.region-editor\s*\{[^}]*outline:\s*none/s);
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
