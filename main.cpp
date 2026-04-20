#include "RandomNumGenerator/RandomGenerators.hpp"
#include <iostream>

int main() {
    TerribleRandGenerator ug;
    ug.init(1234);

    BoxMuller bg(ug);
    std::vector<double> normalVec = bg.getNormalVector(10);

    for (std::size_t i = 0; i < normalVec.size(); ++i) {
        std::cout << normalVec[i] << std::endl;
    }

    return 0;
}