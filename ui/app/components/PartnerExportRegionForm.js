import { NonIdealState, Spinner } from "@blueprintjs/core";
import isEqual from "lodash/isEqual";
import PropTypes from "prop-types";
import React, { Component } from "react";
import { Row, Col, Panel, Button, Table, Modal } from "react-bootstrap";
import {
  Field,
  Fields,
  formValueSelector,
  propTypes,
  reduxForm
} from "redux-form";
import {
  FormattedDate,
  FormattedMessage,
  FormattedRelative,
  FormattedTime
} from "react-intl";
import { connect } from "react-redux";
import { Link } from "react-router-dom";
import "react-select/dist/react-select.css";
import yaml from "js-yaml";
import { fetchGroups } from "../actions/meta";

import ExportAOIField from "./ExportAOIField";
import { getRuns } from "../actions/exports";
import {
  createExportRegion,
  deleteExportRegion,
  getExportRegion,
  runExport,
  updateExportRegion
} from "../actions/partners";
import {
  selectExportRegion,
  selectRuns
} from "../selectors";
import {
  prettyBytes,
  AVAILABLE_EXPORT_FORMATS,
  getFormatCheckboxes,
  renderCheckboxes,
  renderCheckbox,
  renderInput,
  renderTextArea,
  renderSelect,
  renderMultiSelect,
  slugify,
  getRootUrl
} from "./utils";

const FORM_NAME = "PartnerExportRegionForm";

const EXPORT_FORMATS = {
  shp: AVAILABLE_EXPORT_FORMATS.shp,
  geopackage: AVAILABLE_EXPORT_FORMATS.geopackage,
  osm_pbf: AVAILABLE_EXPORT_FORMATS.osm_pbf
};

const form = reduxForm({
  form: FORM_NAME,
  onSubmit: (values, dispatch, { createExportRegion, updateExportRegion }) => {
    console.log("Submitting form. Values:", values);

    const formData = {
      ...values
    };

    if (values.id != null) {
      updateExportRegion(values.id, formData, FORM_NAME);
    } else {
      createExportRegion(formData, FORM_NAME);
    }
  },
  validate: values => {
    const errors = {};

    try {
      yaml.safeLoad(values.feature_selection);
    } catch (err) {
      errors.feature_selection = (
        <pre>
          {err.message}
        </pre>
      );
      errors._error = errors._error || [];
      errors._error.push("Feature selection is invalid.");
    }

    return errors;
  }
});

const getTimeOptions = () => {
  const options = [];

  for (let i = 0; i < 24; i++) {
    options.push(
      <option key={i} value={i}>
        {i}:00 UTC
      </option>
    );
  }

  return options;
};

const PendingDatasetsPanel = ({
  datasetPrefix,
  error,
  featureSelection,
  handleSubmit,
  status,
  submitting
}) =>
  <Panel>
    <Button
      bsStyle="primary"
      type="submit"
      disabled={submitting}
      onClick={handleSubmit}
      block
    >
      {submitting ? "Creating..." : "Create Export"}
    </Button>
    {error &&
      <p>
        <strong>
          {error}
        </strong>
      </p>}
    {status &&
      <p>
        <strong>
          {status}
        </strong>
      </p>}
  </Panel>;

const ExistingDatasetsPanel = ({
  error,
  datasets,
  handleSubmit,
  status,
  submitting
}) =>
  <Panel>
    <Button
      bsStyle="primary"
      type="submit"
      disabled={submitting}
      onClick={handleSubmit}
      block
    >
      {submitting ? "Saving..." : "Save"}
    </Button>
    {error &&
      <p>
        <strong>
          {error}
        </strong>
      </p>}
    {status &&
      <p>
        <strong>
          {status}
        </strong>
      </p>}
  </Panel>;

export class PartnerExportRegionForm extends Component {
  static propTypes = {
    ...propTypes,
    exportRegion: PropTypes.object,
    runs: PropTypes.array
  };

  state = {
    deleting: false,
    editing: false,
    featureSelection: {},
    running: false,
    showDeleteModal: false
  };

  getLastRun() {
    const { exportRegion } = this.props;

    if (exportRegion.last_run == null) {
      return "Never";
    }

    return <FormattedRelative value={exportRegion.last_run} />;
  }

  getNextRun() {
    const { exportRegion } = this.props;

    if (exportRegion.next_run == null) {
      return "Never";
    }

    return <FormattedRelative value={exportRegion.next_run} />;
  }

  didReceiveRegion(exportRegion) {
    if (exportRegion == null) {
      return;
    }

    const { anyTouched, change, getRuns } = this.props;

    getRuns(exportRegion.job_uid);

    // NOTE: this also sets some form properties that we don't care about (but that show up in the onSubmit handler)
    if (!anyTouched) {
      // only update properties if they haven't been touched
      Object.entries(exportRegion).forEach(([k, v]) => change(k, v));

      exportRegion.export_formats.forEach(x => change(x, true));
    }
  }

  didReceiveRuns(exportRegion, runs) {
    if (
      runs[0] != null &&
      ["SUBMITTED", "RUNNING"].indexOf(runs[0].status) >= 0
    ) {
      this.setState({
        running: true
      });

      const { getRuns } = this.props;

      // TODO here's an opportunity for backoff
      this.runTimeout = setTimeout(() => getRuns(exportRegion.job_uid), 15e3);
    } else {
      this.setState({
        running: false
      });
    }
  }

  componentDidMount() {
    const {
      exportRegion,
      getExportRegion,
      fetchGroups,
      match: { params: { id } }
    } = this.props;

    fetchGroups()

    if (id != null) {
      // we're editing
      getExportRegion(id);

      this.setState({
        editing: true
      });

      if (exportRegion != null && exportRegion.id === Number(id)) {
        this.didReceiveRegion(exportRegion);
      }
    }
  }

  componentWillUnmount() {
    if (this.runTimeout != null) {
      clearTimeout(this.runTimeout);
      this.runTimeout = null;
    }
  }

  componentWillReceiveProps(props) {
    const {
      exportRegion: prevExportRegion,
      featureSelection: prevFeatureSelection,
      getExportRegion,
      match: { params: { id: prevId } },
      runs: prevRuns
    } = this.props;

    const {
      exportRegion,
      featureSelection,
      match: { params: { id } },
      runs
    } = props;

    if (prevId !== id) {
      if (id != null) {
        getExportRegion(id);

        if (this.runTimeout != null) {
          clearTimeout(this.runTimeout);
          this.runTimeout = null;
        }

        this.setState({
          editing: true
        });
      } else {
        this.setState({
          editing: false
        });
      }
    }

    if (!isEqual(prevExportRegion, exportRegion)) {
      this.didReceiveRegion(exportRegion);
    }

    if (!isEqual(prevRuns, runs)) {
      this.didReceiveRuns(exportRegion, runs);
    }

    // TODO this would be cleaner if using reselect
    if (prevFeatureSelection !== featureSelection) {
      try {
        this.setState({
          featureSelection: yaml.safeLoad(featureSelection) || {}
        });
      } catch (err) {
        // noop; feature selection may be in the process of being edited
        console.warn(err);
      }
    }
  }

  getGroupOptions() {
   const { groups } = this.props;
   return groups.map(g => 
    <option value={g.id}>{g.name}</option>)
  }


  getRunRows() {
    const { exportRegion, runs } = this.props;

    return runs.slice(0, 10).map((run, i) =>
      <tr key={i}>
        <td>
          <Link to={`/exports/${exportRegion.job_uid}/${run.uid}`}>
            <FormattedDate value={run.started_at} />{" "}
            <FormattedTime value={run.started_at} />
          </Link>
        </td>
        <td>
          {run.status}
        </td>
        <td>
          {`00${Math.floor(run.elapsed_time / 60)}`.slice(-2)}:{`00${Math.round(run.elapsed_time % 60)}`.slice(-2)}
        </td>
        <td>
          {prettyBytes(run.size)}
        </td>
      </tr>
    );
  }

  handleDelete = () => {
    const { exportRegion } = this.props;

    this.setState({
      deleting: true,
      showDeleteModal: true
    });
  };

  handleRun = () => {
    const { exportRegion, runExport } = this.props;

    this.setState({
      running: true
    });

    runExport(exportRegion.id, exportRegion.job_uid);
  };

  render() {
    const { deleting, editing, featureSelection, running, showDeleteModal } = this.state;
    const {
      error,
      exportRegion,
      fetching,
      handleSubmit,
      runs,
      status,
      submitting
    } = this.props;
    const datasetPrefix = this.props.datasetPrefix || "<prefix>";
    const name = this.props.name || "Untitled";

    if (fetching) {
      return (
        <NonIdealState
          action={
            <strong>
              <FormattedMessage id="ui.loading" defaultMessage="Loading..." />
            </strong>
          }
          visual={<Spinner />}
        />
      );
    }

    if (editing && exportRegion == null) {
      return (
        <NonIdealState
          action={
            <strong>
              <FormattedMessage id="ui.hdx.not_found" defaultMessage="Export Region Not Found" />
            </strong>
          }
          visual="warning-sign"
        />
      );
    }

    return (
      <Row style={{ height: "100%" }}>
        <Col xs={6} style={{ height: "100%", overflowY: "scroll" }}>
          <div style={{ padding: "20px"}}>
            <ol className="breadcrumb">
              <li>
                <Link to="/partners">Export Regions</Link>
              </li>
              <li className="active">
                {name}
              </li>
            </ol>
            <form onSubmit={handleSubmit}>
              <h2>
                {editing ? "Edit" : "Create"} Export Region
              </h2>
              {error &&
                <p>
                  <strong>
                    {status}
                  </strong>
                </p>}
              <Field
                name="name"
                type="text"
                label="Name"
                placeholder="Required"
                component={renderInput}
              />
              <Field
                name="event"
                type="text"
                label="Project"
                placeholder=""
                component={renderInput}
              />
              <Field
                name="description"
                type="text"
                label="Description"
                placeholder=""
                component={renderInput}
              />
              <hr />
              <Field
                name="group"
                label="Partner Organization"
                component={renderSelect}
              >
                <option value="">Choose an organization</option>
                {this.getGroupOptions()}
              </Field>
              <hr />
              <Field
                id="formControlsTextarea"
                label="Feature Selection"
                rows="10"
                name="feature_selection"
                component={renderTextArea}
                style={{fontFamily:"monospace"}}
              />
              <hr />
              <Row>
                <Col xs={12}>
                  <Field
                    name="planet_file"
                    description="Use daily planet file: only for huge regions"
                    component={renderCheckbox}
                    type="checkbox"
                  />
                </Col>
              </Row>
              <Row>
                <Col xs={12}>
                  <Field
                    name="polygon_centroid"
                    description="Export polygon centroid"
                    component={renderCheckbox}
                    type="checkbox"
                  />
                </Col>
              </Row>
              <Row>
                <Col xs={6}>
                  <Field
                    name="schedule_period"
                    label="Run this export on an automated schedule:"
                    component={renderSelect}
                  >
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly (Sunday)</option>
                    <option value="monthly">Monthly (1st of month)</option>
                    <option value="6hrs">Every 6 hours</option>
                    <option value="disabled">
                      Don't automatically schedule
                    </option>
                  </Field>
                </Col>
                <Col xs={5} xsOffset={1}>
                  <Field
                    name="schedule_hour"
                    label="At time:"
                    component={renderSelect}
                  >
                    {getTimeOptions()}
                  </Field>
                </Col>
              </Row>
              <Row>
                <Col xs={5}>
                  <Field
                    name="export_formats"
                    label="File Formats"
                    component={renderCheckboxes}
                  >
                    {getFormatCheckboxes(EXPORT_FORMATS)}
                  </Field>
                </Col>
                <Col xs={7}>
                  {editing && exportRegion
                    ? <ExistingDatasetsPanel
                        datasets={exportRegion.datasets}
                        error={error}
                        handleSubmit={handleSubmit}
                        status={status}
                        submitting={submitting}
                      />
                    : <PendingDatasetsPanel
                        datasetPrefix={datasetPrefix}
                        error={error}
                        featureSelection={featureSelection}
                        handleSubmit={handleSubmit}
                        status={status}
                        submitting={submitting}
                      />}
                </Col>
              </Row>
            </form>
            {editing &&
              exportRegion &&
              <div>
                <Row>
                  <Col xs={7}>
                    <p>Permalinks:</p>
                    <Link to={`/exports/${exportRegion.job_uid}`}>
                      Webpage
                    </Link><br/>
                    <a target="_blank" href={`${getRootUrl()}/api/permalink/${exportRegion.job_uid}`}>
                      JSON
                    </a>
                  </Col>
                  <Col xs={5}>
                    <Panel>
                      <p>
                        <strong>Last run:</strong> {this.getLastRun()}
                        <br />
                        <strong>Next scheduled run:</strong> {this.getNextRun()}
                      </p>
                      <Button
                        bsStyle="primary"
                        disabled={running}
                        onClick={this.handleRun}
                      >
                        {running ? "Running..." : "Run Now"}
                      </Button>
                    </Panel>
                  </Col>
                </Row>
                <h3>
                  Run History{" "}
                  <small>
                    <Link to={`/exports/${exportRegion.job_uid}`}>
                      view export details
                    </Link>
                  </small>
                </h3>
                {runs.length > 0
                  ? <Table>
                      <thead>
                        <tr>
                          <th>Run Started</th>
                          <th>Status</th>
                          <th>Elapsed Time</th>
                          <th>Total Size</th>
                        </tr>
                      </thead>
                      <tbody>
                        {this.getRunRows()}
                      </tbody>
                    </Table>
                  : <p>This regional export has never been run.</p>}
                <Panel>
                  <p>
                    This will unschedule the export region.
                  </p>
                  <Button
                    bsStyle="danger"
                    block
                    disabled={deleting}
                    onClick={this.handleDelete}
                  >
                    {deleting
                      ? "Removing Export Region..."
                      : "Remove Export Region"}
                  </Button>
                </Panel>
              </div>}
          </div>
        </Col>
        <Col xs={6} style={{ height: "100%" }}>
          <Fields
            names={["the_geom", "aoi.description", "aoi.geomType", "aoi.title"]}
            component={ExportAOIField}
          />
        </Col>
        <Modal show={showDeleteModal} onHide={() => this.setState({showDeleteModal:false,deleting:false})}>
          <Modal.Header closeButton>
            <Modal.Title>
              <FormattedMessage
                id="ui.exports.confirm_delete.title"
                defaultMessage="Confirm Delete"
              />
            </Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <FormattedMessage
              id="ui.exports.confirm_delete.body"
              defaultMessage="Are you sure you wish to delete this export region?"
            />
          </Modal.Body>
          <Modal.Footer>
            <Button onClick={() => this.setState({showDeleteModal:false, deleting:false})}>
              <FormattedMessage id="ui.cancel" defaultMessage="Cancel" />
            </Button>
            <Button bsStyle="danger" onClick={() => this.props.deleteExportRegion(exportRegion.id)}>
              <FormattedMessage id="ui.delete" defaultMessage="Delete" />
            </Button>
          </Modal.Footer>
        </Modal>
      </Row>
    );
  }
}

const mapStateToProps = (state, ownProps) => ({
  error: state.partners.error,
  exportRegion: selectExportRegion(ownProps.match.params.id, state),
  featureSelection: formValueSelector(FORM_NAME)(state, "feature_selection"),
  fetching: state.partners.fetching,
  initialValues: {
    aoi: {
      description: "Draw",
      geomType: "Polygon",
      title: "Custom Polygon"
    },
    feature_selection: `
Buildings:
  types:
    - polygons
  select:
    - name
    - building
    - building:levels
    - building:materials
    - addr:full
    - addr:housenumber
    - addr:street
    - addr:city
    - office
  where: building IS NOT NULL
`.trim(),
    schedule_period: "daily",
    schedule_hour: 0,
    export_formats: ["shp", "geopackage"],
    group: null,
    planet_file: false,
    polygon_centroid: false
  },
  name: formValueSelector(FORM_NAME)(state, "name"),
  event: formValueSelector(FORM_NAME)(state, "event"),
  description: formValueSelector(FORM_NAME)(state, "description"),
  runs: selectRuns(state),
  status: state.partners.status,
  groups: state.meta.groups || []
});

const flatten = arr =>
  arr.reduce(
    (acc, val) => acc.concat(Array.isArray(val) ? flatten(val) : val),
    []
  );

export default connect(mapStateToProps, {
  createExportRegion,
  getExportRegion,
  getRuns,
  deleteExportRegion,
  runExport,
  updateExportRegion,
  fetchGroups
})(form(PartnerExportRegionForm));
