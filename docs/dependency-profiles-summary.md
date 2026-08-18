# Dependency profile completion summary

Issue #21 originally isolated wallet, signing, Web3, and official SDK packages in an
optional `live` extra so research/PAPER could run without them.

Issue #58 tightens the product boundary further:

- the supported public product is now the minimal non-executing signal producer;
- internal PAPER R&D also uses the minimal runtime;
- the historical `live` extra remains quarantined compatibility infrastructure only;
- public commands contain no execution mode selector or wallet/order operation;
- Continuous Integration (CI) validates the minimal profile plus a transitive
  non-execution import-graph guard;
- public CI no longer installs or advertises the historical live extra.
