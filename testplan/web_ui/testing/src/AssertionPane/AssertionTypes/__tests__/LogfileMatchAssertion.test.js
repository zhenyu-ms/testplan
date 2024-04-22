import React from "react";
import { shallow } from "enzyme";
import { StyleSheetTestUtils } from "aphrodite";

import LogfileMatchAssertion from "../LogfileMatchAssertion";

function defaultProps() {
  return {
    assertion: {
      utc_time: "2020-01-01T00:00:00.000000+00:00",
      machine_time: "2020-01-01T00:00:00.000000+00:00",
      type: "LogfileMatch",
      meta_type: "assertion",
      category: "DEFAULT",
      description: null,
      results: [
        {
          matched: "okok",
          pattern: ".*ok.*",
          timeout: 1.0,
          start_pos: "<BOF>",
          end_pos: "<inode 110000, position 5>"
        },
        {
          matched: "okok",
          pattern: ".*ok.*",
          timeout: 1.0,
          start_pos: "<inode 110000, position 5>",
          end_pos: "<inode 110000, position 10>"
        }
      ],
      failure: [
        {
          matched: null,
          pattern: ".*ok.*",
          timeout: 2.0,
          start_pos: "<inode 110000, position 10>",
          end_pos: "<inode 110000, position 15>"
        }
      ],
    },
  };
}

describe("LogfileMatchAssertion", () => {
  let props;
  let shallowComponent;

  beforeEach(() => {
    // Stop Aphrodite from injecting styles, this crashes the tests.
    StyleSheetTestUtils.suppressStyleInjection();
    props = defaultProps();
    shallowComponent = undefined;
  });

  it("shallow renders the correct HTML structure", () => {
    shallowComponent = shallow(<LogfileMatchAssertion {...props} />);
    expect(shallowComponent).toMatchSnapshot();
  });
});
