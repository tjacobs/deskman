#pragma once

// SDL
#include <SDL2/SDL.h>

// Local
#include "screen.h"

// System
#include <cmath>
#include <vector>

// Namespace
using namespace std;

// Shapes further back shrink by this much per unit of depth
static const float PERSPECTIVE_DEPTH = 0.001f;

// Step this many pixels when filling a shape, then draw a fat dot at each one
static const int SHAPE_FILL_STEP = 2;

// Default shape size and colour when the caller does not say
static const float DEFAULT_CIRCLE_RADIUS = 50;
static const float DEFAULT_ELLIPSE_RADIUS_X = 100;
static const float DEFAULT_ELLIPSE_RADIUS_Y = 50;
static const float DEFAULT_STROKE_WIDTH = 1.0f;
static const SDL_Color DEFAULT_SHAPE_COLOR = {0, 0, 0, 255};

// 3D Vector class for transformations
struct Vec3 {
    float x, y, z;

    // Build a vector, defaulting to the origin
    Vec3(float x = 0, float y = 0, float z = 0) : x(x), y(y), z(z) {}

    // Add two vectors
    Vec3 operator+(const Vec3& other) const {
        return Vec3(x + other.x, y + other.y, z + other.z);
    }

    // Scale a vector
    Vec3 operator*(float scalar) const {
        return Vec3(x * scalar, y * scalar, z * scalar);
    }

    // Turn about the X axis, in degrees
    Vec3 rotateX(float angle) const {
        float radians = angle * M_PI / 180.0f;
        float cosine = cos(radians);
        float sine = sin(radians);
        return Vec3(x, y * cosine - z * sine, y * sine + z * cosine);
    }

    // Turn about the Y axis, in degrees
    Vec3 rotateY(float angle) const {
        float radians = angle * M_PI / 180.0f;
        float cosine = cos(radians);
        float sine = sin(radians);
        return Vec3(x * cosine + z * sine, y, -x * sine + z * cosine);
    }

    // Turn about the Z axis, in degrees
    Vec3 rotateZ(float angle) const {
        float radians = angle * M_PI / 180.0f;
        float cosine = cos(radians);
        float sine = sin(radians);
        return Vec3(x * cosine - y * sine, x * sine + y * cosine, z);
    }
};

// Projection utilities
namespace Projection {
    // Project 3D point to 2D screen coordinates with perspective
    SDL_Point project(const Vec3& point, const Vec3& faceRotation);
}

// Base class for all vector shapes
class VectorShape {
public:
    // Where the shape sits on the face plane, and how it is turned and sized
    Vec3 localPosition;
    Vec3 rotation;
    Vec3 scale;

    // How the shape is painted, and whether it is drawn at all
    SDL_Color fillColor;
    SDL_Color strokeColor;
    float strokeWidth;
    bool visible;

    // Start centred, unturned, full size, and black
    VectorShape() : localPosition(0, 0, 0), rotation(0, 0, 0), scale(1, 1, 1),
                   fillColor(DEFAULT_SHAPE_COLOR), strokeColor(DEFAULT_SHAPE_COLOR), strokeWidth(DEFAULT_STROKE_WIDTH),
                   visible(true) {}
    virtual ~VectorShape() {}

    // Each shape paints itself against the face position and rotation
    virtual void render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) = 0;
};

// Helper function to draw a larger dot
void drawLargeDot(SDL_Renderer* renderer, int x, int y);

// Circle shape
class Circle : public VectorShape {
public:
    float radius;

    // Build a circle of the given radius and colours
    Circle(float radius = DEFAULT_CIRCLE_RADIUS, SDL_Color fillColor = DEFAULT_SHAPE_COLOR,
           SDL_Color strokeColor = DEFAULT_SHAPE_COLOR, float strokeWidth = DEFAULT_STROKE_WIDTH)
        : radius(radius) {
        this->fillColor = fillColor;
        this->strokeColor = strokeColor;
        this->strokeWidth = strokeWidth;
    }

    // Fill the circle a dot at a time, so perspective applies to every point
    void render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) override {
        SDL_SetRenderDrawColor(renderer, fillColor.r, fillColor.g, fillColor.b, fillColor.a);

        // Calculate effective radius based on perspective
        float effectiveRadius = radius * (1.0f / (1.0f + (localPosition.z + facePosition.z) * PERSPECTIVE_DEPTH));

        // Draw filled circle by projecting each point in 3D space
        for (int y = -effectiveRadius; y <= effectiveRadius; y += SHAPE_FILL_STEP) {
            for (int x = -effectiveRadius; x <= effectiveRadius; x += SHAPE_FILL_STEP) {
                if (x * x + y * y > effectiveRadius * effectiveRadius)
                    continue;

                // Move the local point onto the face, then project it
                Vec3 point(x, y, 0);
                point = point + localPosition + facePosition;
                SDL_Point screenPoint = Projection::project(point, faceRotation);
                drawLargeDot(renderer, screenPoint.x, screenPoint.y);
            }
        }
    }
};

// Ellipse shape
class Ellipse : public VectorShape {
public:
    float radiusX, radiusY;

    // Centre and height of an ellipse cut out of this one, for the mouth
    float cutoutY;
    float cutoutHeight;

    // Build an ellipse of the given radii, colours, and cutout
    Ellipse(float radiusX = DEFAULT_ELLIPSE_RADIUS_X, float radiusY = DEFAULT_ELLIPSE_RADIUS_Y,
            SDL_Color fillColor = DEFAULT_SHAPE_COLOR,
            SDL_Color strokeColor = DEFAULT_SHAPE_COLOR, float strokeWidth = DEFAULT_STROKE_WIDTH,
            float cutoutY = 0, float cutoutHeight = 0)
        : radiusX(radiusX), radiusY(radiusY), cutoutY(cutoutY), cutoutHeight(cutoutHeight) {
        this->fillColor = fillColor;
        this->strokeColor = strokeColor;
        this->strokeWidth = strokeWidth;
    }

    // Fill the ellipse a dot at a time, skipping anything inside the cutout
    void render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) override {
        SDL_SetRenderDrawColor(renderer, fillColor.r, fillColor.g, fillColor.b, fillColor.a);

        // Calculate effective radii based on perspective
        float effectiveRadiusX = radiusX * (1.0f / (1.0f + (localPosition.z + facePosition.z) * PERSPECTIVE_DEPTH));
        float effectiveRadiusY = radiusY * (1.0f / (1.0f + (localPosition.z + facePosition.z) * PERSPECTIVE_DEPTH));

        // Draw filled ellipse by projecting each point in 3D space
        for (int y = -effectiveRadiusY; y <= effectiveRadiusY; y += SHAPE_FILL_STEP) {
            for (int x = -effectiveRadiusX; x <= effectiveRadiusX; x += SHAPE_FILL_STEP) {
                // Calculate if point is in the main ellipse
                float ellipseValue = (x * x) / (float)(effectiveRadiusX * effectiveRadiusX) + (y * y) / (float)(effectiveRadiusY * effectiveRadiusY);

                // Calculate if point is in the cutout ellipse
                float cutoutValue = 0.0f;
                if (cutoutHeight > 0) {
                    float cutoutRadiusY = cutoutHeight / 2.0f;
                    float cutoutRadiusX = effectiveRadiusX * (cutoutRadiusY / effectiveRadiusY);
                    cutoutValue = (x * x) / (float)(cutoutRadiusX * cutoutRadiusX) + ((y - cutoutY) * (y - cutoutY)) / (float)(cutoutRadiusY * cutoutRadiusY);
                }

                // Draw point if it's in the main ellipse but not in the cutout
                if (ellipseValue > 1.0f || (cutoutHeight != 0 && cutoutValue <= 1.0f))
                    continue;

                // Move the local point onto the face, then project it
                Vec3 point(x, y, 0);
                point = point + localPosition + facePosition;
                SDL_Point screenPoint = Projection::project(point, faceRotation);
                drawLargeDot(renderer, screenPoint.x, screenPoint.y);
            }
        }
    }
};

// Vector face class to manage all face elements
class VectorFace {
private:
    // Shapes the face owns, and where the whole face sits
    vector<VectorShape*> shapes;
    Vec3 position;
    Vec3 rotation;

public:
    // Start the face centred and facing forward
    VectorFace() : position(0, 0, 0), rotation(0, 0, 0) {}

    // Free every shape handed over
    ~VectorFace() {
        for (auto shape : shapes) {
            delete shape;
        }
    }

    // Take ownership of a shape
    void addShape(VectorShape* shape) {
        shapes.push_back(shape);
    }

    // Turn the whole face
    void setRotation(const Vec3& rotation) {
        this->rotation = rotation;
    }

    // Draw every visible shape
    void render(SDL_Renderer* renderer) {
        for (auto shape : shapes) {
            if (shape->visible)
                shape->render(renderer, position, rotation);
        }
    }
};

// Vector renderer manager
class VectorRenderer {
private:
    // The one face this renderer draws
    VectorFace face;

public:
    // Hand a shape to the face
    void addShape(VectorShape* shape) {
        face.addShape(shape);
    }

    // Draw the face
    void render(SDL_Renderer* renderer) {
        face.render(renderer);
    }

    // Turn the face toward where it is looking
    void setFaceRotation(const Vec3& rotation) {
        face.setRotation(rotation);
    }
};

// Shared renderer instance, defined in main.cpp
extern VectorRenderer vectorRenderer;
