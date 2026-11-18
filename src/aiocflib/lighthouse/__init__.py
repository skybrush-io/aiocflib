"""Estimation of the geometry of Lighthouse base stations from the sweep angles
measured by Crazyflie drones.

The modules in this package need numpy. Install aiocflib with the
``lighthouse`` extra to use them.

The estimation works in the following steps:

1. The drones measure the sweep angles of their Lighthouse sensors at several
   positions. Each set of angles measured at the same time is a *sample*.

2. The functions in `aiocflib.lighthouse.initial_guess` estimate the poses of
   the base stations and the drone in each sample from the angles alone.

3. The solver in `aiocflib.lighthouse.solver` refines the initial guess by
   minimizing the difference between the measured angles and the angles
   predicted from the estimated poses. The result is expressed in the
   reference frame of the initial guess, anchored to one of the base stations.

4. The functions in `aiocflib.lighthouse.alignment` transform the result into
   the coordinate system of the room, using samples taken at the origin, on
   the X axis and in the XY plane.
"""
