export function createInitialState(data) {
  const defaultSample = data.samples[0];
  const defaultDimension = defaultSample.dimensionOrder[0];
  const defaultObservations = defaultSample.observationsByDimension[defaultDimension] || [];
  const defaultObservation = defaultObservations[0];

  return {
    data: structuredClone(data),
    activeStudent: defaultSample.id,
    activeDimension: defaultDimension,
    activeObservationId: defaultObservation?.id || null,
    openObservationIds: defaultObservations.map((item) => item.id),
    activeSpanId: defaultObservation?.evidence[0]?.spanId || null,
    releasedStudents: Object.fromEntries(data.samples.map((sample) => [sample.id, false])),
    selectionMenu: {
      visible: false,
      x: 0,
      y: 0,
      entryId: null,
      text: "",
    },
    inspectorScrollTop: 0,
    pageScrollY: 0,
    toast: "",
    toastTimer: null,
  };
}
