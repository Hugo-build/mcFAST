------- OpenFAST example input file -------------------------------------------
"IEA-15-240-RWT_ElastoDyn.dat"   EDFile   - Name of ElastoDyn input file
True                               Echo     - Echo input data to <RootName>.ech (flag)
600.0                              TMax     - Total run time (s)
0.0125                             DT       - Recommended module time step (s)
Default                            DT_Out   - Output time step (s)

OutList                 - The next lines are an unmodified table/list section
"RotSpeed"
"GenPwr"
END

