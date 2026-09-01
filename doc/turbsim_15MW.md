
# Turbsim for  IEA 15MW UMaine-semi FOWT

The exact `turbsim` input file in the repository [IEA 15MW](https://github.com/IEAWindSystems/IEA-15-240-RWT.git) suits mostly for the bottom fixed prototype, a recommended input file for the semi-submersible floating system is available here:

```
---------TurbSim v2.00.* Input File------------------------
IEA 15 MW UMaineSemi default, adapted from master.inp for TurbSim v4.2.1.
---------Runtime Options-----------------------------------
False         Echo            - Echo input data to <RootName>.ech (flag)
101           RandSeed1       - First random seed (-2147483648 to 2147483647)
"RANLUX"      RandSeed2       - Second random seed, or alternative pRNG: "RanLux" or "RNSNLW"
False         WrBHHTP         - Output hub-height turbulence parameters in binary form? (Generates RootName.bin)
False         WrFHHTP         - Output hub-height turbulence parameters in formatted form? (Generates RootName.dat)
False         WrADHH          - Output hub-height time-series data in AeroDyn form? (Generates RootName.hh)
True          WrADFF          - Output full-field time-series data in TurbSim/AeroDyn form? (Generates RootName.bts)
False         WrBLFF          - Output full-field time-series data in BLADED/AeroDyn form? (Generates RootName.wnd)
False         WrADTWR         - Output tower time-series data? (Generates RootName.twr)
False         WrHAWCFF        - Output full-field time-series data in HAWC form?
False         WrFMTFF         - Output full-field time-series data in formatted form? (Generates RootName.u, RootName.v, RootName.w)
False         WrACT           - Output coherent turbulence time steps in AeroDyn form? (Generates RootName.cts)
0             ScaleIEC        - Scale IEC turbulence models to exact target standard deviation? [0=no; 1=hub; 2=individual]

--------Turbine/Model Specifications-----------------------
48            NumGrid_Z       - Vertical grid-point matrix dimension
48            NumGrid_Y       - Horizontal grid-point matrix dimension
0.05          TimeStep        - Time step [seconds]
60.0          AnalysisTime    - Length of analysis time series [seconds]
"ALL"         UsableTime      - Usable length of output time series [seconds], or "ALL"
150.0         HubHt           - Hub height [m] (should be > 0.5*GridHeight)
296.0         GridHeight      - Grid height [m] (covers 2 to 298 m above MSL)
300.0         GridWidth       - Grid width [m] (includes floating-platform and rotor margin)
0.0           VFlowAng        - Vertical mean flow (uptilt) angle [degrees]
0.0           HFlowAng        - Horizontal mean flow (skew) angle [degrees]

--------Meteorological Boundary Conditions-------------------
"IECKAI"      TurbModel       - IEC Kaimal turbulence model
"unused"      UserFile        - User spectra/time-series file (unused for IECKAI)
"3"           IECstandard     - IEC 61400 standard number
B             IECturbc        - IEC turbulence category
"NTM"         IEC_WindType    - IEC normal turbulence model
"default"     ETMc            - IEC ETM c parameter [m/s]
"PL"          WindProfileType - Power-law velocity profile
"unused"      ProfileFile     - User profile file (unused for PL)
150.0         RefHt           - Reference wind-speed height [m]
12          URef            - Mean wind speed at RefHt [m/s]
350.0         ZJetMax         - Jet height [m] (unused for PL)
0.14          PLExp           - Power-law exponent
0.0003        Z0              - Surface roughness length [m]

--------Non-IEC Meteorological Boundary Conditions------------
"default"     Latitude        - Site latitude [degrees]
0.05          RICH_NO         - Gradient Richardson number
"default"     UStar           - Friction or shear velocity [m/s]
"default"     ZI              - Mixing layer depth [m]
"default"     PC_UW           - Hub mean u'w' Reynolds stress [m^2/s^2]
"default"     PC_UV           - Hub mean u'v' Reynolds stress [m^2/s^2]
"default"     PC_VW           - Hub mean v'w' Reynolds stress [m^2/s^2]

--------Spatial Coherence Parameters----------------------------
"default"     SCMod1          - u-component coherence model
"default"     SCMod2          - v-component coherence model
"default"     SCMod3          - w-component coherence model
"default"     InCDec1         - u-component coherence parameters
"default"     InCDec2         - v-component coherence parameters
"default"     InCDec3         - w-component coherence parameters
"default"     CohExp          - Coherence exponent

--------Coherent Turbulence Scaling Parameters-------------------
"EventData"   CTEventPath     - Path containing coherent-event data
"Random"      CTEventFile     - Event-file type (LES, DNS, or RANDOM)
True          Randomize       - Randomize disturbance scale and locations?
1.0           DistScl         - Disturbance scale
0.5           CTLy            - Fractional lateral location of tower centerline
0.5           CTLz            - Fractional vertical location of hub height
30.0          CTStartTime     - Minimum coherent-structure start time [seconds]

====================================================
! NOTE: Do not add or remove any lines in this file!
====================================================


```