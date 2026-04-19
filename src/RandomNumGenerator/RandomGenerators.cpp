#include "RandomGenerators.hpp"
#include <cmath>
#include <vector>
#include <cstdlib>

TerribleRandGenerator::TerribleRandGenerator()
    : factor(1.0 / (static_cast<double>(RAND_MAX) + 1.0)) {}

void TerribleRandGenerator::init(long seed) {
    std::srand(static_cast<unsigned int>(seed));
}

double TerribleRandGenerator::getUniform() {
    return (std::rand() + 0.5) * factor;
}

BoxMuller::BoxMuller(UniformGenerator& uniformGen)
    : NormalGenerator(uniformGen), U1(0.0), U2(0.0), N1(0.0), N2(0.0),
      W(0.0), TWO_PI(6.28318530717958647692) {}

std::vector<double> NormalGenerator::getNormalVector(long n) {
    std::vector<double> values(n);
    for (long i = 0; i < n; ++i) {
        values[i] = getNormal();
    }
    return values;
}

double BoxMuller::getNormal() {
    U1 = uniformGen->getUniform();
    U2 = uniformGen->getUniform();
    W = std::sqrt(-2.0 * std::log(U1));

    N1 = W * std::cos(TWO_PI * U2);
    N2 = W * std::sin(TWO_PI * U2);

    return N1;
}