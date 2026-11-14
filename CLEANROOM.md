# Clean-room rules

sublevels reimplements protocols that existing GPL software implements (eiskaltdcpp, DC++,
verlihub, and our own forks of them). To keep this repository MIT, its code is written without
reference to that software's source.

## Roles

- **Spec writers** may read anything: public protocol documentation, the GPL implementations,
  and our fork notes (the private transfix/nmdc-dev archive and the transfix forks). They write
  `spec/` in their own words. It describes behaviour on the wire (messages, fields, order,
  errors, timing) and never source code, identifiers, structure or comments from an
  implementation.
- **Implementers** work only from `spec/`, the public documentation it cites, and this
  repository. They do not open the GPL implementations or the fork notes while working here.

## Allowed sources for implementers

- `spec/` in this repository.
- Public protocol documents that `spec/` cites, such as the NMDC protocol wiki and the ADC
  specification.
- Packet captures of interoperability tests against any client or hub, run as a black box.

## Provenance

- Every file in `spec/` lists its sources.
- A pull request that adds implementation code says which spec sections it implements.
- A contributor who has read a GPL implementation's source for a protocol area writes spec for
  that area, not code.
