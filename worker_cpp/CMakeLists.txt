cmake_minimum_required(VERSION 3.15)
project(DeCFD_Worker CXX)
set(CMAKE_CXX_STANDARD 17)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()
# Static MSVC runtime: the math library (sin/cos in the rasterizer) becomes part of the binary
# whose hash every task pins, instead of a system DLL that differs between Windows versions.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
add_executable(worker main.cpp)
# Put the binary next to the sources (worker_cpp/worker[.exe]), where the orchestrator looks for it.
# The generator expression stops multi-config generators (Visual Studio) from adding a Release/ subfolder.
set_target_properties(worker PROPERTIES RUNTIME_OUTPUT_DIRECTORY $<1:${CMAKE_CURRENT_SOURCE_DIR}>)
if(MSVC)
  # /O2 clashes with the /RTC1 that MSVC adds to Debug builds
  target_compile_options(worker PRIVATE /openmp $<$<NOT:$<CONFIG:Debug>>:/O2>)
else()
  target_compile_options(worker PRIVATE -O3 -fopenmp)
  target_link_libraries(worker PRIVATE -fopenmp)
endif()
