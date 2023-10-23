import { DateRangeInput } from "@blueprintjs/datetime";
import React, { Component } from "react";
import { Button, FormControl, InputGroup, Row } from "react-bootstrap";
import { FormattedMessage, defineMessages, injectIntl } from "react-intl";
import { connect } from "react-redux";
import { Field, Fields, propTypes, reduxForm } from "redux-form";

import { selectIsLoggedIn } from "../selectors";
import { renderCheckbox,renderSelect } from "./utils";

const form = reduxForm({
  form: "ExportSearchForm"
});

const messages = defineMessages({
  showAll: {
    id: "ui.search.show_all",
    defaultMessage: "Show all {type}"
  },
  searchPlaceholder: {
    id: "ui.search.default.placeholder",
    defaultMessage: "Name or description"
  }
});

const renderDateRange = fields =>
  <DateRangeInput
    allowSingleDayRange
    contiguousCalendarMonths={false}
    maxDate={new Date()}
    onChange={([after, before]) => {
      fields.after.input.onChange(after);
      fields.before.input.onChange(before);
    }}
    value={[fields.after.input.value, fields.before.input.value]}
  />;

class Input extends Component {
  render() {
    const { input, meta, type, ...props } = this.props;

    return <FormControl {...input} type={type} {...props} />;
  }
}
class ExportSearchForm extends Component {
  static propTypes = {
    ...propTypes
  };

  render() {
    const {
      handleSubmit,
      hideAll ,
      intl: { formatMessage },
      isLoggedIn,
      running,
      showDateRange,
      showFrequency,
      type
    } = this.props;
    let { searchPlaceholder } = this.props;

    if (searchPlaceholder == null) {
      searchPlaceholder = messages.searchPlaceholder;
    }

    return (
      <form onSubmit={this.search}>
        <Row>
          <div
            className="pt-input-group pt-large"
            style={{
              width: "100%",
              marginTop: "20px",
              marginBottom: "10px"
            }}
          >
            <i className="pt-icon pt-icon-search" />
            <Field
              className="pt-input pt-round"
              component={Input}
              componentClass="input"
              name="search"
              placeholder={formatMessage(searchPlaceholder)}
            />
          </div>
        </Row>
        { showFrequency && <Row>
          <Field
            name="schedule_period"
            label="Filter by frequency"
            component={renderSelect}
          >
            <option value="any">Any</option>
            <option value="6hrs">Every 6 hours</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly (Sunday)</option>
            <option value="2wks">Every two weeks</option>
            <option value="3wks">Every three weeks</option>
            <option value="monthly">Monthly (1st of month)</option>
            <option value="quarterly">Quarterly</option>
            <option value="semiyearly">Every 6 Months</option>
            <option value="yearly">Yearly</option>
            <option value="disabled">
              Don't automatically schedule : Run as needed
            </option>
          </Field>
        </Row>
        }
        {showDateRange &&
          <Row>
            <Button
              bsStyle="primary"
              className="pull-right"
              disabled={running}
              style={{
                marginTop: "10px"
              }}
              type="submit"
              onClick={handleSubmit}
            >
              <FormattedMessage id="ui.search" defaultMessage="Search" />
            </Button>
            <InputGroup
              style={{
                width: "70%",
                marginTop: "10px",
                marginRight: "20px",
                marginBottom: "10px"
              }}
            >
              <InputGroup.Addon>Date Range:</InputGroup.Addon>
              <Fields component={renderDateRange} names={["before", "after"]} />
            </InputGroup>
          </Row>}
        <Row>
          {showDateRange ||
            <Button
              bsStyle="primary"
              className="pull-right"
              disabled={running}
              style={{
                marginTop: "10px"
              }}
              type="submit"
              onClick={handleSubmit}
            >
              <FormattedMessage id="ui.search" defaultMessage="Search" />
            </Button>}
          {isLoggedIn && !hideAll &&
            <Field
              component={renderCheckbox}
              description={formatMessage(messages.showAll, {
                type
              })}
              name="all"
              // allow the change to propagate before submitting
              onChange={() => setImmediate(handleSubmit)}
              type="checkbox"
            />}
        </Row>
      </form>
    );
  }
}

const mapStateToProps = state => ({
  isLoggedIn: selectIsLoggedIn(state)
});

export default connect(mapStateToProps)(form(injectIntl(ExportSearchForm)));
