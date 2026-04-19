using namespace std;
#include <vector>

class UniformGenerator {
public:
    virtual void init(long seed) = 0;
    virtual double getUniform() = 0;
    virtual ~UniformGenerator() = default;
};

class TerribleRandGenerator : public UniformGenerator {
    // based on infamous rand() that is why it is terrible
    private:
        double factor;
    public:
        TerribleRandGenerator();

        // Initialize the seed
        void init(long Seed_);

        // Implement the variant hook function
        double getUniform();
};

class NormalGenerator {
protected:
    UniformGenerator* uniformGen;
public:
    NormalGenerator(UniformGenerator& uniformGen) : uniformGen(&uniformGen) {}
    virtual double getNormal() = 0;
    std::vector<double> getNormalVector(long N);
};

class BoxMuller : public NormalGenerator {
    private:
        double U1, U2; // Uniform Numbers
        double N1, N2; // 2 Normal numbers as a produyct of BM

        double W;
        const double TWO_PI;

    public:
        BoxMuller(UniformGenerator& uniformGen);

        // Implement the variant hook function
        double getNormal();
};