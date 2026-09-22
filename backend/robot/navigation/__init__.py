"""Algorithmic navigation pipeline for the Python AMR.

The pipeline is intentionally composed of replaceable components. Each component
is described by an interface in :mod:`interfaces`; the deterministic baseline
implementation lives in this package under the ``Algorithmic*`` names. Later, AI
modes can swap individual components (e.g. an RL collision-avoidance layer) while
keeping the deterministic safety and coordination layers intact.
"""