import React, { Component, PropTypes } from "react";
import isEqual from "lodash/isEqual";
import Attribution from "ol/control/attribution";
import Fill from "ol/style/fill";
import Circle from "ol/style/circle";
import GeoJSONFormat from "ol/format/geojson";
import interaction from "ol/interaction";
import LayerAttribution from "ol/attribution";
import Map from "ol/map";
import OSM from "ol/source/osm";
import proj from "ol/proj";
import ScaleLine from "ol/control/scaleline";
import Stroke from "ol/style/stroke";
import Style from "ol/style/style";
import Tile from "ol/layer/tile";
import VectorLayer from "ol/layer/vector";
import VectorSource from "ol/source/vector";
import View from "ol/view";
import Zoom from "ol/control/zoom";
import bbox from "@turf/bbox";

import FilterControl from "./FilterControl";
import ZoomExtent from "./ZoomExtent";
import styles from "../styles/aoi/CreateExport.css";

const GEOJSON_FORMAT = new GeoJSONFormat();
const WGS84 = "EPSG:4326";
const WEB_MERCATOR = "EPSG:3857";

export default class MapListView extends Component {
  static propTypes = {
    features: PropTypes.shape({
      features: PropTypes.array,
      geomType: PropTypes.string,
      type: PropTypes.string
    }),
    selectedFeatureId: PropTypes.number
  };

  state = {
    _features: []
  };

  componentDidMount() {
    this._initializeOpenLayers();

    if (this.props.features) {
      this.updateFeatures(this.props.features);
    }
    if (this.props.selectedFeatureId) {
      this.zoomToFeatureId(this.props.selectedFeatureId);
    }
  }

  componentDidUpdate(prevProps, prevState) {
    const { features, selectedFeatureId } = this.props;

    if (!isEqual(prevProps.features, features)) {
      this.updateFeatures(features);
    }

    if (prevProps.selectedFeatureId !== selectedFeatureId) {
      this.zoomToFeatureId(selectedFeatureId);
    }
  }

  updateFeatures(features) {
    this._clearDraw();

    GEOJSON_FORMAT.readFeatures(features, {
      dataProjection: WGS84,
      featureProjection: WEB_MERCATOR
    }).forEach(feature => {
      this._drawLayer.getSource().addFeature(feature);
    });
  }

  zoomToFeatureId(id) {
    const { features } = this.props;

    const selectedFeature = features.features.filter(x => x.id === id).shift();

    if (selectedFeature != null) {
      this._map
        .getView()
        .fit(
          proj.transformExtent(bbox(selectedFeature), WGS84, WEB_MERCATOR),
          this._map.getSize()
        );
    }
  }

  _clearDraw() {
    this._drawLayer.getSource().clear();
  }

  _generateDrawLayer() {

    const fill = new Fill({
          color: "hsla(202, 70%, 50%, .35)"
        })
    return new VectorLayer({
      source: new VectorSource({
        wrapX: false
      }),
      style: new Style({
        fill: fill,
        stroke: new Stroke({
          color: "hsla(202, 70%, 50%, .7)",
          width: 1,
          lineDash: [5, 5]
        }),
        image: new Circle({
          fill: fill,
          radius: 5
        })
      })
    });
  }

  _initializeOpenLayers() {
    const { onUpdate } = this.props;

    this._map = new Map({
      controls: [
        new ScaleLine({
          className: styles.olScaleLine
        }),
        new Attribution({
          collapsible: false,
          collapsed: false
        }),
        new Zoom({
          className: styles.olZoom
        }),
        new ZoomExtent({
          className: styles.olZoomToExtent,
          extent: [
            -14251567.50789682,
            -10584983.780136958,
            14251787.50789682,
            10584983.780136958
          ]
        })
      ],
      interactions: interaction.defaults({
        keyboard: false,
        altShiftDragRotate: false,
        pinchRotate: false
      }),
      layers: [
        // Order matters here
        new Tile({
          source: new OSM({
            attributions: [
              new LayerAttribution({
                html: `© <a href="https://osm.org/">OSM</a>`
              }),
              OSM.ATTRIBUTION
            ],
            url:
              "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          })
        })
      ],
      target: "map",
      view: new View({
        projection: WEB_MERCATOR,
        center: [110, 0],
        zoom: 2,
        minZoom: 2,
        maxZoom: 22
      })
    });

    if (onUpdate) {
      this._map.addControl(
        new FilterControl({
          onUpdate
        })
      );
    }

    this._drawLayer = this._generateDrawLayer();
    this._map.addLayer(this._drawLayer);
  }

  render() {
    return (
      <div>
        <div id="map" className={styles.map} ref="olmap" />
      </div>
    );
  }
}
